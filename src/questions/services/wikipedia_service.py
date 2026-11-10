import httpx

from questions.services.text_utils import clean_extract, extract_keywords

WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"

HEADERS = {
    # Wikimedia asks API clients to identify themselves with a contact point.
    "User-Agent": "QueryForge/0.1 (Django demo project; https://github.com/pavithra-droid/queryforge)"
}

REQUEST_TIMEOUT = 10.0


def extract_search_query(question: str) -> str:
    """Strip question filler so Wikipedia searches on the topic words."""
    return " ".join(extract_keywords(question))


def search_wikipedia(question: str, limit: int = 5) -> list[dict]:
    """
    Search Wikipedia and return the lead section of the top results,
    ordered by Wikipedia's own relevance ranking.

    Uses a single `generator=search` request so search results and their
    extracts arrive together. Only the lead section (`exintro`) is fetched:
    the TextExtracts API returns full-article text for just one page per
    request, whereas intro extracts work for many pages at once, and the
    lead is where Wikipedia summarises the topic.
    """
    search_query = extract_search_query(question)

    if not search_query:
        return []

    response = httpx.get(
        WIKIPEDIA_API_URL,
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": search_query,
            "gsrnamespace": 0,
            "gsrlimit": limit,
            "prop": "extracts|info",
            "exintro": 1,
            "explaintext": 1,
            "exlimit": "max",
            "inprop": "url",
            "redirects": 1,
            "format": "json",
            "formatversion": 2,
        },
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    pages = response.json().get("query", {}).get("pages", [])

    # `index` is the page's position in the search results.
    pages = sorted(pages, key=lambda page: page.get("index", 0))

    documents = []

    for page in pages:
        content = clean_extract(page.get("extract") or "")

        if page.get("title") and content:
            documents.append(
                {
                    "title": page["title"],
                    "url": page.get("fullurl", ""),
                    "content": content,
                }
            )

    return documents
