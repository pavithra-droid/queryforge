from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest

from questions.providers.ollama_provider import OllamaProvider


@pytest.fixture
def ollama_provider():
    return OllamaProvider(
        base_url="http://localhost:11434",
        model="llama3.2:3b",
        timeout=120,
    )


@pytest.fixture
def documents():
    return [
        SimpleNamespace(
            title="Django",
            content="Django is a Python web framework.",
        ),
        SimpleNamespace(
            title="Python",
            content="Python is a programming language.",
        ),
    ]


@patch("questions.providers.ollama_provider.httpx.post")
def test_generate_answer_returns_llm_response(
    mock_post,
    ollama_provider,
    documents,
):
    mock_response = Mock()
    mock_response.json.return_value = {
        "message": {"content": "Django is a Python web framework."}
    }
    mock_post.return_value = mock_response

    result = ollama_provider.generate_answer(
        "What is Django?",
        documents,
    )

    assert result == "Django is a Python web framework."

    mock_post.assert_called_once()

    request_args = mock_post.call_args

    assert request_args.args[0] == "http://localhost:11434/api/chat"
    assert request_args.kwargs["json"]["model"] == "llama3.2:3b"
    assert request_args.kwargs["json"]["stream"] is False
    assert request_args.kwargs["timeout"] == 120


@patch("questions.providers.ollama_provider.httpx.post")
def test_generate_answer_includes_question_and_context(
    mock_post,
    ollama_provider,
    documents,
):
    mock_response = Mock()
    mock_response.json.return_value = {
        "message": {"content": "Django is a web framework."}
    }
    mock_post.return_value = mock_response

    ollama_provider.generate_answer(
        "What is Django?",
        documents,
    )

    payload = mock_post.call_args.kwargs["json"]
    messages = payload["messages"]

    assert "What is Django?" in messages[1]["content"]
    assert "Django is a Python web framework." in messages[1]["content"]
    assert "Python is a programming language." in messages[1]["content"]


@patch("questions.providers.ollama_provider.httpx.post")
def test_generate_answer_does_not_call_ollama_without_documents(
    mock_post,
    ollama_provider,
):
    result = ollama_provider.generate_answer(
        "What is Django?",
        [],
    )

    assert result == ("I couldn't find relevant information to answer your question.")

    mock_post.assert_not_called()


@patch("questions.providers.ollama_provider.httpx.post")
def test_generate_answer_raises_timeout(
    mock_post,
    ollama_provider,
    documents,
):
    mock_post.side_effect = httpx.TimeoutException("Ollama request timed out")

    with pytest.raises(httpx.TimeoutException):
        ollama_provider.generate_answer(
            "What is Django?",
            documents,
        )


@patch("questions.providers.ollama_provider.httpx.post")
def test_generate_answer_raises_http_error(
    mock_post,
    ollama_provider,
    documents,
):
    request = httpx.Request(
        "POST",
        "http://localhost:11434/api/chat",
    )
    response = httpx.Response(
        503,
        request=request,
    )

    mock_response = Mock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Service unavailable",
        request=request,
        response=response,
    )
    mock_post.return_value = mock_response

    with pytest.raises(httpx.HTTPStatusError):
        ollama_provider.generate_answer(
            "What is Django?",
            documents,
        )


@patch("questions.providers.ollama_provider.httpx.post")
def test_generate_answer_raises_error_for_empty_response(
    mock_post,
    ollama_provider,
    documents,
):
    mock_response = Mock()
    mock_response.json.return_value = {"message": {"content": ""}}
    mock_post.return_value = mock_response

    with pytest.raises(
        ValueError,
        match="Ollama returned an empty answer",
    ):
        ollama_provider.generate_answer(
            "What is Django?",
            documents,
        )
