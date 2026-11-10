from unittest.mock import Mock, patch

import httpx
import pytest

from questions.services.text_utils import clean_extract, extract_keywords
from questions.services.wikipedia_service import (
    extract_search_query,
    search_wikipedia,
)


def _response(pages):
    response = Mock()
    response.json.return_value = {"query": {"pages": pages}}
    return response


def test_extract_keywords_drops_question_filler():
    assert extract_keywords("What is Redis and what is it used for ..") == [
        "redis",
        "used",
    ]


def test_extract_keywords_keeps_topic_words_in_order():
    assert extract_keywords("Who wrote Python programming language?") == [
        "wrote",
        "python",
        "programming",
        "language",
    ]


def test_extract_search_query_handles_punctuation():
    assert extract_search_query("What's Django? Is it useful!") == ("s django useful")


def test_clean_extract_removes_pronunciation_debris():
    assert clean_extract("Redis (; Remote Dictionary Server) is") == (
        "Redis (Remote Dictionary Server) is"
    )
    assert clean_extract("keep (see below)") == "keep (see below)"


@patch("questions.services.wikipedia_service.httpx.get")
def test_search_wikipedia_uses_one_request_and_keeps_search_rank(mock_get):
    # The API returns pages in arbitrary order; `index` is the search rank.
    mock_get.return_value = _response(
        [
            {
                "title": "Flask (web framework)",
                "fullurl": "https://en.wikipedia.org/wiki/Flask",
                "extract": "Flask is a micro web framework.",
                "index": 2,
            },
            {
                "title": "Django (web framework)",
                "fullurl": "https://en.wikipedia.org/wiki/Django",
                "extract": "Django is a Python web framework.",
                "index": 1,
            },
        ]
    )

    result = search_wikipedia("What is Django web framework?")

    assert [doc["title"] for doc in result] == [
        "Django (web framework)",
        "Flask (web framework)",
    ]
    assert result[0] == {
        "title": "Django (web framework)",
        "url": "https://en.wikipedia.org/wiki/Django",
        "content": "Django is a Python web framework.",
    }

    mock_get.assert_called_once()
    params = mock_get.call_args.kwargs["params"]
    assert params["gsrsearch"] == "django web framework"
    # Regression guard: without exintro, TextExtracts returns only one page.
    assert params["exintro"] == 1


@patch("questions.services.wikipedia_service.httpx.get")
def test_search_wikipedia_returns_empty_list_when_no_results(mock_get):
    response = Mock()
    response.json.return_value = {"batchcomplete": True}
    mock_get.return_value = response

    assert search_wikipedia("Unknown topic") == []
    mock_get.assert_called_once()


@patch("questions.services.wikipedia_service.httpx.get")
def test_search_wikipedia_skips_pages_without_extract(mock_get):
    mock_get.return_value = _response(
        [
            {"title": "Django", "extract": "Django is a web framework.", "index": 1},
            {"title": "Empty page", "extract": "", "index": 2},
            {"title": "Missing extract", "index": 3},
        ]
    )

    result = search_wikipedia("Django")

    assert [doc["title"] for doc in result] == ["Django"]


@patch("questions.services.wikipedia_service.httpx.get")
def test_search_wikipedia_raises_timeout(mock_get):
    mock_get.side_effect = httpx.TimeoutException("Request timed out")

    with pytest.raises(httpx.TimeoutException):
        search_wikipedia("Django")


@patch("questions.services.wikipedia_service.httpx.get")
def test_search_wikipedia_raises_http_error(mock_get):
    request = httpx.Request("GET", "https://en.wikipedia.org/w/api.php")
    response = httpx.Response(429, request=request)

    mock_response = Mock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Too many requests",
        request=request,
        response=response,
    )
    mock_get.return_value = mock_response

    with pytest.raises(httpx.HTTPStatusError):
        search_wikipedia("Django")


@patch("questions.services.wikipedia_service.httpx.get")
def test_search_wikipedia_does_not_call_api_for_empty_query(mock_get):
    assert search_wikipedia("What is it?") == []
    mock_get.assert_not_called()
