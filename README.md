# QueryForge

Ask a question over a REST API and get an answer built from Wikipedia in the
background, with its sources cited.

A Celery worker searches Wikipedia, re-ranks the articles with PostgreSQL
full-text search, and sends the best ones to a pluggable answer provider. The
answer comes from a deterministic offline provider by default, or from a local
LLM through Ollama. The API never waits on Wikipedia or the model. You post a
question, get `202 Accepted`, and poll until it has an answer.

---

## Quick start

**You need:** Docker with Docker Compose v2. No API keys, accounts or `.env`
file.

```bash
docker compose up --build
```

This starts five containers:

| Service  | What it does |
|----------|--------------|
| `web`    | Django + DRF API on <http://localhost:8000>. Runs migrations on start. |
| `worker` | Celery worker that processes questions |
| `beat`   | Celery beat. Re-queues questions stuck in `PROCESSING` (see below). |
| `db`     | PostgreSQL 16 |
| `redis`  | Celery broker |

**Ask a question:**

```bash
curl -s -X POST http://localhost:8000/api/questions/ \
  -H 'Content-Type: application/json' \
  -d '{"text": "What is Redis and what is it used for?"}'
# → 202 {"id": 1, "status": "PENDING", "answer": "", ...}

curl -s http://localhost:8000/api/questions/1/
# → 200 {"id": 1, "status": "COMPLETED", "answer": "Redis (Remote Dictionary Server) is an
#        in-memory key–value database, used as a distributed cache and message broker...
#        Source: Redis (https://en.wikipedia.org/wiki/Redis)",
#        "source_documents": [{"title": "Redis", "url": "..."}, ...]}
```

The answer is usually ready within 1–2 seconds.

> **Port already in use?** If Postgres or Redis already runs on your machine,
> run `POSTGRES_HOST_PORT=5433 REDIS_HOST_PORT=6380 WEB_PORT=8001 docker compose up --build`.
> Those host ports are published only for local development; the containers
> talk to each other over the Compose network.

---

## API

Base URL: `http://localhost:8000/api/`

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/questions/` | Create a question. Returns `202` and queues it for processing. |
| `GET`  | `/questions/` | List questions, newest first, paginated. |
| `GET`  | `/questions/{id}/` | Get one question with its answer and sources. |

Other methods return `405`.

**Question resource**

```json
{
  "id": 1,
  "text": "What is Redis and what is it used for?",
  "status": "COMPLETED",
  "answer": "Redis (Remote Dictionary Server) is an in-memory key–value database... \n\nSource: Redis (https://en.wikipedia.org/wiki/Redis)",
  "error_message": null,
  "source_documents": [{"title": "Redis", "url": "https://en.wikipedia.org/wiki/Redis"}],
  "created_at": "2026-11-10T16:21:23.787993Z",
  "updated_at": "2026-11-10T16:21:24.355526Z"
}
```

- `status` is one of `PENDING`, `PROCESSING`, `COMPLETED` or `FAILED`.
- `error_message` is set only when `status` is `FAILED`. It's a friendly
  message, never a stack trace.
- `text` is required, trimmed, and at most 500 characters.

**Pagination:** responses look like `{"count", "next", "previous", "results"}`.
The default page size is 20. Use `?page=2` to change page and `?page_size=50`
to change the size (maximum 100).

**Errors:** every error has the same shape.

```json
{"error": {"code": "validation_error", "message": "The request data is invalid.",
           "details": {"text": ["This field may not be blank."]}}}
```

| Code | Status | Meaning |
|------|--------|---------|
| `validation_error` | 400 | Invalid body. `details` lists the problem for each field. |
| `parse_error` | 400 | Malformed JSON |
| `not_found` | 404 | Unknown question or page |
| `method_not_allowed` | 405 | For example `DELETE` |
| `throttled` | 429 | Too many new questions. Read the `Retry-After` header. |
| `server_error` | 500 | Unexpected error. The traceback is logged, not returned. |

**Rate limit:** creating questions is limited to 10 per minute per client IP
(`QUESTION_CREATE_RATE`). Each new question costs a Wikipedia call. Reading
and polling are never throttled.

### Example requests

```bash
B=http://localhost:8000/api/questions/

# Create and poll until done
ID=$(curl -s -X POST $B -H 'Content-Type: application/json' \
      -d '{"text": "Who wrote the Python programming language?"}' \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
watch -n1 "curl -s $B$ID/ | python3 -m json.tool"

# List, newest first, 5 per page
curl -s "$B?page_size=5" | python3 -m json.tool

# Errors
curl -s -X POST $B -H 'Content-Type: application/json' -d '{"text": ""}'     # 400 validation_error
curl -s -X POST $B -H 'Content-Type: application/json' -d '{"text":'         # 400 parse_error
curl -s ${B}999999/                                                          # 404 not_found
curl -s -X DELETE ${B}1/                                                     # 405 method_not_allowed
```

Example questions that work well: *what is django web framework*, *who wrote
python programming language*, *what is redis and what is it used for*.

---

## Running the tests and linting

```bash
# Inside Docker (with the stack running)
docker compose exec web pytest -q

# Or locally with uv (needs Postgres from `docker compose up db redis`)
uv sync
uv run pytest -q          # 53 tests
uv run ruff check .       # lint
uv run ruff format .      # format
```

The tests need PostgreSQL, not SQLite, because retrieval uses Postgres
full-text search.

### Running Django outside Docker (development)

```bash
docker compose up -d db redis
uv sync
uv run python manage.py migrate
uv run python manage.py runserver                 # terminal 1
uv run celery -A config worker -l info            # terminal 2
uv run celery -A config beat -l info              # terminal 3 (optional)
```

No `.env` is needed: the defaults match `docker-compose.yml`. See
`.env.example` for every setting you can change.

---

## Track and data source

**Track 1, "Chat with your sources".** I chose it because the hard part is
what happens around the model: slow calls, failures, retries, and jobs that run
twice or die halfway. That's backend reliability work, which is what this role
is about.

**Wikipedia, through the MediaWiki Action API** (`/w/api.php`). It's free,
needs no key, and covers almost any general question.

The brief suggests the REST `page/summary` endpoint, but that needs one call
per article. The Action API with `generator=search` returns the search results
and their intro text in **one request**. Fewer calls means fewer chances to
fail, and less of Wikipedia's per-IP rate limit used up.

---

## Architecture

```
 client ──POST /api/questions/──▶ Django/DRF ──insert row (PENDING)──▶ PostgreSQL
   │                                  │
   │◀──────── 202 Accepted ───────────┤ transaction.on_commit → enqueue task
   │                                  ▼
   │                               Redis (broker)
   │                                  │
   │                                  ▼
   │                            Celery worker
   │                     1. lock row, mark PROCESSING
   │                     2. Wikipedia search (top 5 intros) ──▶ en.wikipedia.org
   │                     3. store SourceDocuments
   │                     4. re-rank with Postgres FTS, keep top 3
   │                     5. answer via provider (offline | ollama)
   │                     6. save answer, mark COMPLETED
   │
   └──GET /api/questions/{id}/ (poll) ──▶ status + answer + sources
```

The code is split into layers, so each piece can be tested on its own:

| Layer | Files | Responsibility |
|---|---|---|
| API | `views.py`, `serializers.py`, `pagination.py`, `throttling.py`, `exceptions.py` | HTTP, validation, error shape, throttling |
| Task | `tasks.py` | Retry policy and the final failure state |
| Orchestration | `services/process_service.py` | Idempotency and the status changes |
| Integrations | `services/wikipedia_service.py`, `providers/*` | External calls only |
| Retrieval | `services/retrieval_service.py`, `services/text_utils.py` | Ranking and text handling |

### Data model

- **`Question`**: `text`, `status` (`PENDING` / `PROCESSING` / `COMPLETED` /
  `FAILED`), `answer`, `error_message`, `created_at`, `updated_at`.
- **`SourceDocument`**: a foreign key to `Question` (indexed), plus `title`,
  `url` and `content`. Each question gets its own copy of its retrieved
  articles. That keeps the answer reproducible: the sources shown are exactly
  what the answer was built from, even if Wikipedia changes later.

### How the background work is handled

| Concern | How it's handled |
|---|---|
| Keep slow work out of the request | `POST` only inserts a row and returns `202`. The worker does the rest. |
| Task running before the row exists | The task is queued with `transaction.on_commit`, so it can't start before the insert is committed. |
| Duplicate delivery or a double click | `process_question` locks the row with `select_for_update`. A `COMPLETED` question is skipped. A `PROCESSING` one is skipped unless it's stale. |
| Network errors, timeouts, 429, 5xx | Automatic retry: up to 3 times, exponential backoff (2s base, 30s cap) with jitter. |
| Permanent errors (other 4xx) | No retry. The question fails straight away. |
| Retries after a failed attempt | A failed attempt resets the question to `PENDING`, so the retry can take it. Without this, a retry would see `PROCESSING` and do nothing. |
| All retries used up | `on_failure` marks it `FAILED` with a friendly `error_message`. |
| Worker killed mid-task | `acks_late` and `reject_on_worker_lost` put the task back on the queue. `prefetch_multiplier=1` stops one worker holding many tasks. |
| Task hangs | Soft and hard time limits (150s and 180s). Every HTTP call also has a timeout. |
| Question stuck in `PROCESSING` | Celery beat runs `recover_stale_questions` every 5 minutes and re-queues anything in `PROCESSING` for more than 10 minutes. |

### Retrieval

1. **Query:** remove filler words ("what is", "tell me"…), so the search
   for *"What is Redis and what is it used for?"* is `redis used`.
2. **Search:** one Wikipedia request returns the top 5 articles' intro
   sections, kept in Wikipedia's ranking order.
3. **Re-rank:** PostgreSQL full-text search over title (weight A) and body
   (weight B). Keywords are combined with **OR**, so a question with an extra
   word still matches. More matched keywords give a higher rank. If nothing
   matches, it falls back to Wikipedia's order.

### Answer providers

The `LLMProvider` interface has two implementations. `LLM_PROVIDER` chooses
between them.

- **`offline`** (default) is deterministic and needs no key or network. It
  picks the 2–3 sentences of the best article that share the most words with
  the question, then adds the source. The same input always gives the same
  output, which makes it easy to test.
- **`ollama`** uses a local LLM. Its system prompt says to answer only from the
  supplied context, and to say it doesn't know otherwise.

### Key decisions and trade-offs

| Decision | Why | Trade-off |
|---|---|---|
| Action API `generator=search` + `exintro` | 1 request instead of 1 + N. Full-article text is only returned **for one page per request**; intro sections work for many pages at once. | The answer can only come from the intro section. That's usually where the definition is. |
| Postgres FTS instead of embeddings | The brief allows it. No extra service or model. Easy to explain. | No semantic matching ("car" won't match "automobile"). |
| `simple` config for titles, `english` for body | The English stemmer turns "redis" into "redi", which made the unrelated article **Redi** rank first. Titles are names, so they shouldn't be stemmed. | The title has to contain the exact word. |
| `normalization=0` for ranking | Dividing by document length made short stub articles beat the main one (Django CMS above Django). | Long articles that repeat a word a lot can rank a bit higher. |
| Polling instead of WebSockets | Simplest to run and test. The brief says polling is fine. | Clients make extra requests while they wait. |
| Page-number pagination ordered by `-id` | Familiar for clients. Ordering by the primary key uses its index, so no migration was needed. | `COUNT(*)` gets slower as the table grows. Cursor pagination would avoid it. |
| A small stop-word list instead of spaCy | spaCy was loaded but not used. It added a large dependency and a model download, and slowed start-up. | English only. The list is written by hand. |
| `runserver` in Docker | One command, auto-reload, nothing else to set up. | It's not a production server (see below). |

---

## Configuration

Everything has a working default.

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `offline` | `offline` or `ollama` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` (Docker: `http://ollama:11434`) | Address of the Ollama server |
| `OLLAMA_MODEL` | `llama3.2:3b` | Model name |
| `OLLAMA_TIMEOUT` | `60` (Docker: `120`) | Seconds per LLM call |
| `QUESTION_CREATE_RATE` | `10/minute` | Throttle on `POST /questions/` |
| `QUESTION_PROCESSING_TIMEOUT` | `600` | Seconds before a `PROCESSING` question counts as stuck |
| `QUESTION_RECOVERY_INTERVAL` | `300` | Seconds between recovery runs |
| `DJANGO_SECRET_KEY` / `DJANGO_DEBUG` / `DJANGO_ALLOWED_HOSTS` | dev values | **Must be set in production** |
| `POSTGRES_*`, `REDIS_URL` | match `docker-compose.yml` | Connections |

### Optional: answers from a real LLM with Ollama

Ollama runs as a container, so nothing needs installing on your machine. It's
an optional Compose *profile*, so the default `docker compose up` doesn't
download several GB for it.

```bash
LLM_PROVIDER=ollama docker compose --profile ollama up --build
```

This adds two services:

- **`ollama`**: the Ollama server (official `ollama/ollama` image). Downloaded
  models are kept in the `ollama_data` volume, so they're fetched only once.
- **`ollama-pull`**: a one-off job that downloads `OLLAMA_MODEL` and exits.

The first run downloads the Ollama image (about 8.5 GB on disk, mostly GPU
libraries) and the model (`llama3.2:3b` is about 2 GB). Wait for the
log line `ollama-pull-1 exited with code 0` before asking questions. A question
asked before the model is ready fails, because Ollama answers `404 model not
found` and that error isn't retried.

On a laptop without a GPU, a smaller model is faster:

```bash
LLM_PROVIDER=ollama OLLAMA_MODEL=llama3.2:1b docker compose --profile ollama up --build
```

Ollama runs on the CPU by default. For an NVIDIA GPU, uncomment the `deploy:`
block in `docker-compose.yml` (this needs `nvidia-container-toolkit`).

**Already have Ollama installed on the host?** Point the containers at it
instead, and leave out the profile:
`LLM_PROVIDER=ollama OLLAMA_BASE_URL=http://host.docker.internal:11434 docker compose up`.
On Linux, the host's Ollama must listen on `0.0.0.0` (set `OLLAMA_HOST` in
`systemctl edit ollama`).

Docker Compose also reads a `.env` file in the project folder, so a local
`.env` with `LLM_PROVIDER=ollama` switches the provider too.

---

## Testing and QA

### Approach

The tests cover the places where a bug would hurt in production: duplicate or
lost work, bad retries, wrong answers, and API contract breaks. Coverage
numbers weren't the goal. Every external call (Wikipedia, Ollama, Celery
dispatch) is mocked in unit tests. Retrieval tests run against real
PostgreSQL, because the ranking logic lives in SQL.

| Area | What the tests check |
|---|---|
| Wikipedia client (`test_wikipedia_service.py`) | One request; results sorted by search rank; pages without text skipped; timeouts and HTTP errors raised; no call when the question has no keywords. A regression guard makes sure `exintro` stays set. |
| Retrieval (`test_retrieval_service.py`) | Filler words don't block a match; the main article beats a stub and the stemming lookalike ("Redis" vs "Redi"); falls back to Wikipedia order; never returns another question's documents. |
| Processing (`test_process_service.py`) | Happy path; `COMPLETED` and fresh `PROCESSING` are skipped; stale `PROCESSING` is processed again; **a failed attempt releases the question so the retry can process it**. |
| Task (`test_tasks.py`) | Retries on network errors and 503; no retry on 400; the final failure marks `FAILED`. |
| Providers (`test_ollama_provider.py`, offline tests) | Prompt contents, timeouts, empty responses; offline answers come from the best article only and cite it. |
| API (`test_api.py`) | `202` and the task is queued after commit; validation and length limit; error shape for 400/404/405/429; pagination (pages don't overlap, size is capped); throttling; **list query count stays fixed (no N+1)**. |

### Query plans

With 10,000 questions and 50,000 source documents, the list endpoint runs 3
queries no matter how big the page is (`test_list_does_not_query_per_question`).
Both main queries use indexes:

```
-- page: ORDER BY id DESC LIMIT 20
Limit  (actual time=0.025..0.033 rows=20)
  ->  Index Scan Backward using questions_question_pkey on questions_question
Execution Time: 0.065 ms

-- prefetch of source documents for that page
Index Scan using questions_sourcedocument_question_id_c8cea067 on questions_sourcedocument
  Index Cond: (question_id = ANY ('{10003,10002,...}'::bigint[]))
Execution Time: 0.123 ms
```

### Manual test plan

Run these with the stack up (`B=http://localhost:8000/api/questions/`).

| # | Scenario | Steps | Expected |
|---|---|---|---|
| 1 | Happy path | `POST` *"what is django web framework"*, then poll | `202` and `PENDING`, then `COMPLETED` in about 2s; answer from *Django (web framework)* with a source line |
| 2 | Nothing to search | `POST` *"what is it"* | `COMPLETED`, "I couldn't find relevant information…", no sources, no Wikipedia call |
| 3 | Validation | Blank text, 501 characters, `{}`, broken JSON | `400` with `validation_error` or `parse_error` |
| 4 | Not found / method | `GET ${B}999999/`, `DELETE ${B}1/` | `404 not_found`, `405 method_not_allowed` |
| 5 | Pagination | Create more than 5 questions, then `GET $B?page_size=5` and follow `next` | Newest first, no repeats across pages, `?page=999` gives `404` |
| 6 | Throttle | Send 11 `POST`s within a minute | The 11th returns `429 throttled` with `Retry-After`; `GET` still works |
| 7 | Worker down | `docker compose stop worker`, `POST`, then `docker compose start worker` | Stays `PENDING`, then completes once the worker is back (the task waited in Redis) |
| 8 | Wikipedia unreachable | Disconnect the network, then `POST` | Worker log shows 3 retries with growing waits, then `FAILED` with a friendly `error_message` |
| 9 | LLM unreachable | Ollama profile running, then `docker compose stop ollama` and `POST` | Same as 8: retried, then `FAILED` |
| 10 | Duplicate delivery | `docker compose exec web python manage.py shell -c "from questions.tasks import process_question_task as t; t.delay(1); t.delay(1)"` | One task does the work; the other returns straight away; no duplicate sources |
| 11 | Stuck job | Set a question to `PROCESSING` with `updated_at` 15 minutes ago | Within 5 minutes beat re-queues it and it completes |

### What I'd check next with more time

- Load test `POST` together with polling, to size the worker pool and the throttle.
- Kill the worker in the middle of a slow Ollama call, many times over, and
  confirm the task is always redelivered.
- Build a small set of questions with expected answers and track answer
  quality whenever the retrieval code changes.
- Test Wikipedia responses with odd or missing fields
  (`missing`, `invalid`, empty `pages`).

---

## Known limitations

- **One question, one answer.** There are no conversations, so there's no
  ordering across a user's messages. Track 1 describes a chat, and this was a
  deliberate cut to fit the time.
- **No authentication.** Questions are visible to everyone, and the throttle
  is per IP.
- **The throttle counter is kept in each process's memory**, so with several
  `web` processes each one counts separately.
- **Each retry fetches Wikipedia again,** even when only the LLM call failed.
- **The offline answers only pick sentences by shared words.** They can include
  a sentence that makes no sense on its own (for example a "he…" sentence), and
  they don't understand meaning.
- **English only:** English Wikipedia, English stop words, a simple sentence
  splitter (abbreviations like "e.g." can split a sentence).
- **No caching or merging of repeated questions.** The same question asked
  twice is processed twice.
- **Wikipedia rate-limits by IP.** A burst of questions can get 429s. They're
  retried, but the answers take longer.
- The recovery query on `status`/`updated_at` has no index. That's fine at this
  size, and a partial index would fix it later.

## What I'd change before production

**Running it.** Replace `runserver` with gunicorn (or uvicorn), and serve static
files properly. Set `DJANGO_DEBUG=false`, a real `DJANGO_SECRET_KEY` from a
secrets manager, `ALLOWED_HOSTS`, HTTPS, and the security headers. Run the
migrations as a separate deploy step, not on every container start. Add health
and readiness endpoints, and run the worker and beat as separately scaled
services (exactly one beat).

**Security and multiple users.** Add token or session auth and link each
question to its owner, with a permission check so users only see their own
questions. Move the throttle counters to a shared Redis cache and add
per-user limits.

**Reliability.** Store the Wikipedia results so an LLM retry doesn't fetch
them again. Cache popular searches in Redis for a short time. Add a circuit
breaker so that while Wikipedia is down, questions fail fast instead of
queueing retries. Make the LLM call its own retryable step. Send logs as
structured JSON with the question ID on every line, and add metrics (queue
depth, task time, failure rate per error type) and alerts.

**Product.** Add `Conversation` and `Message` models with a sequence number
per conversation, so a chat has a fixed reply order. Use Server-Sent Events
instead of polling. Add embeddings (pgvector) as a second ranking step for
meaning-based matches. Use cursor pagination once the table is large.

---

## How I used AI

I used Claude (Claude Code) as a pair programmer for reviewing code, debugging
and drafting. Every change was checked by running the real pipeline against
live Wikipedia and PostgreSQL, not just the unit tests.

**Where it helped**

- It found that Wikipedia returns full-article text **for one page per
  request** (and limits `exchars` to 1,200). The old code sent 10 page IDs,
  silently got back one mostly random article, and gave poor answers.
- It found that the original re-ranking required every word of the question
  to match, so most questions matched nothing.
- While testing, a real Wikipedia 429 exposed a retry bug: a failed attempt
  left the question in `PROCESSING`, so every retry did nothing.
- It set up the Docker Compose stack, Ruff, the error handler, the throttle
  and their tests.

**Where its output was wrong and was corrected**

- Its first re-ranking fix still ranked the article **Redi** above **Redis**
  (stemming) and stubs above the main article (length normalisation). Testing
  the ranking settings on the stored documents showed the problem, which led
  to `simple` + `english` and `normalization=0`.
- Its first offline answers mixed sentences from different articles (a Flask
  sentence in a Django answer). Changed to answer from the best article only.
- Ruff's automatic fix turned the stop-word list into one 600-character
  line. That fix was rejected and the readable version kept.

