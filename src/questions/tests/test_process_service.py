from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from django.utils import timezone

from questions.models import Question
from questions.providers.offline_provider import OfflineLLMProvider
from questions.services.answer_service import generate_answer
from questions.services.process_service import process_question


def test_generate_answer_with_documents():
    documents = [
        SimpleNamespace(
            title="Django",
            url="https://en.wikipedia.org/wiki/Django",
            content=(
                "Django is a Python web framework. "
                "It was released in 2005. "
                "Bananas are yellow."
            ),
        ),
        SimpleNamespace(
            title="Python",
            url="https://en.wikipedia.org/wiki/Python",
            content="Python is a programming language.",
        ),
    ]

    with patch(
        "questions.services.answer_service.get_llm_provider"
    ) as mock_get_provider:
        mock_get_provider.return_value = OfflineLLMProvider()

        answer = generate_answer("What is Django?", documents)

    # Answers from the top document only, with sentences that match the
    # question, and cites its source.
    assert "Django is a Python web framework." in answer
    assert "Bananas" not in answer
    assert "Python is a programming language." not in answer
    assert "Source: Django (https://en.wikipedia.org/wiki/Django)" in answer


def test_offline_answer_skips_documents_without_matching_sentences():
    documents = [
        SimpleNamespace(title="Unrelated", url="", content="Nothing here."),
        SimpleNamespace(
            title="Redis", url="", content="Redis is an in-memory database."
        ),
    ]

    answer = OfflineLLMProvider().generate_answer("What is Redis?", documents)

    assert answer.startswith("Redis is an in-memory database.")


def test_generate_answer_without_documents():
    answer = generate_answer("What is Django?", [])

    assert answer == ("I couldn't find relevant information to answer your question.")


@pytest.mark.django_db
@patch("questions.services.process_service.generate_answer")
@patch("questions.services.process_service.retrieve_relevant_documents")
@patch("questions.services.process_service.save_source_documents")
@patch("questions.services.process_service.search_wikipedia")
def test_process_question_successfully(
    mock_search,
    mock_save,
    mock_retrieve,
    mock_generate,
):
    question = Question.objects.create(text="What is Django?")

    mock_search.return_value = [
        {
            "title": "Django",
            "url": "https://example.com/django",
            "content": "Django is a Python web framework.",
        }
    ]
    mock_retrieve.return_value = []
    mock_generate.return_value = "Django is a Python web framework."

    result = process_question(question.id)
    result.refresh_from_db()

    assert result.status == Question.Status.COMPLETED
    assert result.answer == "Django is a Python web framework."

    mock_search.assert_called_once_with("What is Django?")
    mock_save.assert_called_once()
    mock_retrieve.assert_called_once()
    mock_generate.assert_called_once()


@pytest.mark.django_db
@patch("questions.services.process_service.search_wikipedia")
def test_completed_question_is_not_processed_again(mock_search):
    question = Question.objects.create(
        text="What is Django?",
        status=Question.Status.COMPLETED,
        answer="Already processed.",
    )

    result = process_question(question.id)

    assert result.status == Question.Status.COMPLETED
    assert result.answer == "Already processed."
    mock_search.assert_not_called()


@pytest.mark.django_db
@patch("questions.services.process_service.search_wikipedia")
def test_processing_question_is_not_processed_again(mock_search):
    question = Question.objects.create(
        text="What is Django?",
        status=Question.Status.PROCESSING,
    )

    result = process_question(question.id)

    assert result.status == Question.Status.PROCESSING
    mock_search.assert_not_called()


@pytest.mark.django_db
@patch("questions.services.process_service.generate_answer")
@patch("questions.services.process_service.retrieve_relevant_documents")
@patch("questions.services.process_service.save_source_documents")
@patch("questions.services.process_service.search_wikipedia")
def test_stale_processing_question_can_be_reprocessed(
    mock_search,
    mock_save,
    mock_retrieve,
    mock_generate,
):
    question = Question.objects.create(
        text="What is Django?",
        status=Question.Status.PROCESSING,
    )

    Question.objects.filter(id=question.id).update(
        updated_at=timezone.now() - timedelta(minutes=15)
    )

    mock_search.return_value = []
    mock_retrieve.return_value = []
    mock_generate.return_value = "Django is a web framework."

    result = process_question(question.id)

    assert result.status == Question.Status.COMPLETED
    assert result.answer == "Django is a web framework."

    mock_search.assert_called_once()
    mock_generate.assert_called_once()


@pytest.mark.django_db
@patch("questions.services.process_service.generate_answer")
@patch("questions.services.process_service.retrieve_relevant_documents")
@patch("questions.services.process_service.save_source_documents")
@patch("questions.services.process_service.search_wikipedia")
def test_failed_attempt_releases_claim_so_retry_can_process(
    mock_search,
    mock_save,
    mock_retrieve,
    mock_generate,
):
    """
    Regression: a failed attempt (e.g. Wikipedia 429) left the question in
    PROCESSING, so the Celery retry assumed another worker owned it and
    returned without doing anything.
    """
    question = Question.objects.create(text="What is Django?")

    request = httpx.Request("GET", "https://en.wikipedia.org/w/api.php")
    rate_limited = httpx.HTTPStatusError(
        "Too many requests",
        request=request,
        response=httpx.Response(429, request=request),
    )
    mock_search.side_effect = [rate_limited, []]
    mock_retrieve.return_value = []
    mock_generate.return_value = "Django is a web framework."

    with pytest.raises(httpx.HTTPStatusError):
        process_question(question.id)

    question.refresh_from_db()
    assert question.status == Question.Status.PENDING

    result = process_question(question.id)

    assert result.status == Question.Status.COMPLETED
    assert result.answer == "Django is a web framework."
    assert mock_search.call_count == 2
