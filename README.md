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

Pick one of two ways to run it. Both expose the API on <http://localhost:8000>.

### Method 1: offline answers (default, fastest)

```bash
docker compose up --build        # foreground, logs in the terminal
docker compose up --build -d     # or detached (background)
```

Answers come from the deterministic offline provider: no model download, no
GPU. This starts five containers:

| Service  | What it does |
|----------|--------------|
| `web`    | Django + DRF API on <http://localhost:8000>. Runs migrations on start. |
| `worker` | Celery worker that processes questions |
| `beat`   | Celery beat. Every 5 minutes it re-queues questions stuck in `PROCESSING` for over 10 minutes (for example after a worker crash). |
| `db`     | PostgreSQL 16 |
| `redis`  | Celery broker |

### Method 2: answers from a local LLM (Ollama)

```bash
LLM_PROVIDER=ollama docker compose --profile ollama up --build -d
```

This adds two containers: `ollama` (the server) and `ollama-pull` (a one-off job
that downloads `OLLAMA_MODEL`, default `llama3.2:3b`, about 2 GB, plus about
8.5 GB for the image). Models are kept in the `ollama_data` volume, so they
download only once. **Wait until the pull finishes** before calling the API or running any tests:

```bash
docker compose --profile ollama logs -f ollama-pull    # ends with "success"
docker compose --profile ollama ps -a                   # ollama-pull: Exited (0)
```

On a laptop without a GPU, a smaller model is faster:
`LLM_PROVIDER=ollama OLLAMA_MODEL=llama3.2:1b docker compose --profile ollama up --build -d`.

### Check it is running, and see logs

```bash
docker compose ps                    # all services should be "running"/"healthy"
docker compose logs -f worker        # watch questions being processed
```

### Stop it after testing

```bash
docker compose down                      # Method 1: stop and remove containers
docker compose --profile ollama down     # Method 2: also stops ollama
```

Plain `docker compose down` does **not** stop the `ollama` containers, because
they belong to a profile. Always include `--profile ollama` if you started
Method 2. Your data is kept for next time. To also delete the database and the
downloaded models (frees about 10 GB), add `-v`:
`docker compose --profile ollama down -v`.

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
> run `POSTGRES_HOST_PORT=5433 REDIS_HOST_PORT=6380 WEB_PORT=8001 docker compose up --build -d`.
> Those host ports are published only for local development; the containers
> talk to each other over the Compose network.

---

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/questions/` | Create a question. Returns `202` and queues it. |
| `GET`  | `/api/questions/` | List questions, oldest first, paginated (`?page=`, `?page_size=` max 100). |
| `GET`  | `/api/questions/{id}/` | One question with `status` (`PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`), `answer`, `error_message` and `source_documents`. |

`text` is required, trimmed, at most 500 characters. Every error has the same
shape: `{"error": {"code", "message", "details"}}` with codes `validation_error`
and `parse_error` (400), `not_found` (404), `method_not_allowed` (405),
`throttled` (429, with `Retry-After`) and `server_error` (500, traceback logged
only). Creating questions is limited to 10 per minute per IP.

### Example requests

```bash
B=http://localhost:8000/api/questions/

# Create and poll until done
ID=$(curl -s -X POST $B -H 'Content-Type: application/json' \
      -d '{"text": "Who wrote the Python programming language?"}' \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
watch -n1 "curl -s $B$ID/ | python3 -m json.tool"

# List, oldest first, 5 per page
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

**Track 1, "Chat with your sources"**, because the hard part is what happens
around the model: slow calls, failures, retries and jobs that run twice or die
halfway. **Wikipedia through the MediaWiki Action API**: free, no key, and
covers most general questions. `generator=search` with `exintro` returns the
results and their intro text in one request, instead of the REST `page/summary`
endpoint's one call per article, so there are fewer failure points and less
rate limit used.

## Architecture

```
 client ──POST──▶ Django/DRF ──insert row (PENDING)──▶ PostgreSQL
   │                  │ on_commit → enqueue
   │◀─── 202 ─────────┤──▶ Redis ──▶ Celery worker
   │                                  1. lock row, mark PROCESSING
   │                                  2. Wikipedia search (top 5 intros)
   │                                  3. store SourceDocuments
   │                                  4. re-rank with Postgres FTS, keep top 3
   │                                  5. answer via provider (offline | ollama)
   └──GET (poll) ──▶ status + answer  6. save answer, mark COMPLETED
```

**Data model.** `Question` (`text`, `status`, `answer`, `error_message`,
timestamps) and `SourceDocument` (indexed FK to `Question`, `title`, `url`,
`content`). Each question keeps its own copy of its sources, so the answer is
reproducible even if Wikipedia changes.

**Background work.**
- `POST` only inserts a row and returns `202`; the task is queued with
  `transaction.on_commit`, so it can't run before the row exists.
- Duplicate delivery is safe: `select_for_update` on the row, `COMPLETED` is
  skipped, fresh `PROCESSING` is skipped, stale `PROCESSING` is retaken.
- Network errors, timeouts, 429 and 5xx retry up to 3 times with exponential
  backoff and jitter. Other 4xx fail immediately. A failed attempt resets the
  question to `PENDING` so the retry can take it. When retries run out the
  question is `FAILED` with a friendly message.
- `acks_late` + `reject_on_worker_lost` redeliver tasks from a killed worker.
  Task time limits and HTTP timeouts stop hangs. Celery beat re-queues
  questions stuck in `PROCESSING` for over 10 minutes.

**Retrieval and answers.** Filler words are stripped, Wikipedia returns the top
5 intros, and PostgreSQL full-text search (title weight A, body weight B,
keywords OR-ed) re-ranks them. The `offline` provider (default, deterministic,
no key) picks the best sentences from the top article. The `ollama` provider
uses a local LLM told to answer only from the supplied context.

**Trade-offs.**
- Postgres FTS instead of embeddings: no extra service, easy to explain, but no
  semantic matching.
- `simple` config for titles and `english` for body, `normalization=0`: stemming
  ranked the article *Redi* above *Redis*, and length normalisation put stubs
  above the main article.
- Intro text only: one request, and usually where the definition is.
- Polling instead of WebSockets: simplest to run and test.
- `runserver` in Docker: one command, but not a production server.

---

## Testing and QA

53 tests cover where bugs would hurt: duplicate or lost work, retries, ranking,
and API contract. External calls (Wikipedia, Ollama, Celery) are mocked;
retrieval tests use real PostgreSQL because the ranking is SQL. Areas: Wikipedia
client, retrieval, processing idempotency (including the failed-attempt reset),
task retry rules, providers, and the API (error shape, pagination, throttling,
no N+1 on the list endpoint).

### Manual test plan

Run these with the stack up (`B=http://localhost:8000/api/questions/`).

| # | Scenario | Steps | Expected |
|---|---|---|---|
| 1 | Happy path | `POST` *"what is django web framework"*, then poll | `202` and `PENDING`, then `COMPLETED` in about 2s; answer from *Django (web framework)* with a source line |
| 2 | Nothing to search | `POST` *"what is it"* | `COMPLETED`, "I couldn't find relevant information…", no sources, no Wikipedia call |
| 3 | Validation | Blank text, 501 characters, `{}`, broken JSON | `400` with `validation_error` or `parse_error` |
| 4 | Not found / method | `GET ${B}999999/`, `DELETE ${B}1/` | `404 not_found`, `405 method_not_allowed` |
| 5 | Pagination | Create more than 5 questions, then `GET $B?page_size=5` and follow `next` | Oldest first (1, 2, 3…), no repeats across pages, `?page=999` gives `404` |
| 6 | Throttle | Send 11 `POST`s within a minute | The 11th returns `429 throttled` with `Retry-After`; `GET` still works |
| 7 | Worker down | `docker compose stop worker`, `POST`, then `docker compose start worker` | Stays `PENDING`, then completes once the worker is back (the task waited in Redis) |
| 8 | Wikipedia unreachable | Disconnect the network, then `POST` | Worker log shows 3 retries with growing waits, then `FAILED` with a friendly `error_message` |
| 9 | LLM unreachable | Ollama profile running, then `docker compose stop ollama` and `POST` | Same as 8: retried, then `FAILED` |
| 10 | Duplicate delivery | `docker compose exec web python manage.py shell -c "from questions.tasks import process_question_task as t; t.delay(1); t.delay(1)"` | One task does the work; the other returns straight away; no duplicate sources |
| 11 | Stuck job | Set a question to `PROCESSING` with `updated_at` 15 minutes ago | Within 5 minutes beat re-queues it and it completes |

---

## Known limitations

- One question, one answer: no conversations (a deliberate cut for time).
- No authentication; the throttle is per IP and kept in each process's memory.
- Each retry fetches Wikipedia again, even if only the LLM call failed.
- Offline answers pick sentences by shared words, so they can read oddly.
- English only; no caching of repeated questions.
- Wikipedia rate-limits by IP, so bursts get 429s (retried, but slower).
- The stuck-question recovery query has no index (fine at this size).

## What I'd change before production

- **Run:** gunicorn instead of `runserver`, `DEBUG=false`, a secret key from a
  secrets manager, HTTPS and security headers, migrations as a separate deploy
  step, health endpoints, and separately scaled worker and a single beat.
- **Security:** token auth with per-user questions and permissions; throttle
  counters in shared Redis.
- **Reliability:** store retrieved sources so an LLM retry doesn't refetch, cache
  popular searches, add a circuit breaker for Wikipedia outages, make the LLM
  call its own retryable step, and add structured logs, metrics and alerts.
- **Product:** `Conversation` and `Message` models with per-conversation
  ordering, Server-Sent Events instead of polling, pgvector for semantic
  ranking, and cursor pagination.

## How I used AI

I used AI tools as a pair programmer for review, debugging and drafting, and
checked every change against live Wikipedia and PostgreSQL, not only the unit
tests.

- **Helped:** found that Wikipedia returns full text for one page per request
  (the old code sent 10 IDs and got one random article); found that re-ranking
  required every word to match; a real 429 exposed the retry bug where a failed
  attempt stayed `PROCESSING`; set up Compose, Ruff, the error handler and
  throttle.
- **Overruled:** its first ranking fix still put *Redi* above *Redis* and stubs
  above the main article, so I tested settings on stored documents and chose
  `simple` + `english` with `normalization=0`; its offline answers mixed
  sentences from different articles, so they now use the best article only; I
  rejected a Ruff auto-fix that turned the stop-word list into one 600-character
  line.
