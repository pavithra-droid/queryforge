from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from questions.models import Question, SourceDocument
from questions.serializers import QUESTION_MAX_LENGTH
from questions.throttling import QuestionCreateThrottle


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
@patch("questions.views.process_question_task.apply_async")
def test_create_question_returns_202_and_schedules_task(
    mock_apply_async,
    api_client,
    django_capture_on_commit_callbacks,
):
    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(
            "/api/questions/",
            {"text": "What is Django?"},
            format="json",
        )

    assert response.status_code == status.HTTP_202_ACCEPTED

    question = Question.objects.get(id=response.data["id"])
    assert question.status == Question.Status.PENDING

    mock_apply_async.assert_called_once_with(args=[question.id])


@pytest.mark.django_db
def test_create_question_without_text_returns_400(api_client):
    response = api_client.post(
        "/api/questions/",
        {},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_failed_question_exposes_error_message(api_client):
    question = Question.objects.create(
        text="What is Django?",
        status=Question.Status.FAILED,
        error_message="Processing failed.",
    )

    response = api_client.get(f"/api/questions/{question.id}/")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["error_message"] == "Processing failed."


@pytest.mark.django_db
def test_get_question_includes_source_documents(question):
    SourceDocument.objects.create(
        question=question,
        title="Django",
        url="https://en.wikipedia.org/wiki/Django",
        content="Django is a Python web framework.",
    )

    client = APIClient()
    response = client.get(f"/api/questions/{question.id}/")

    assert response.status_code == 200

    data = response.json()

    assert len(data["source_documents"]) == 1
    assert data["source_documents"][0] == {
        "title": "Django",
        "url": "https://en.wikipedia.org/wiki/Django",
    }


@pytest.mark.django_db
def test_source_documents_are_read_only(question):
    SourceDocument.objects.create(
        question=question,
        title="Django",
        url="https://en.wikipedia.org/wiki/Django",
        content="Django content",
    )

    client = APIClient()
    response = client.patch(
        f"/api/questions/{question.id}/",
        {"source_documents": []},
        format="json",
    )

    # PATCH is disabled by the current viewset configuration.
    assert response.status_code == 405


@pytest.mark.django_db
def test_question_text_longer_than_limit_returns_400(api_client):
    response = api_client.post(
        "/api/questions/",
        {"text": "a" * (QUESTION_MAX_LENGTH + 1)},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "text" in response.data["error"]["details"]
    assert not Question.objects.exists()


@pytest.mark.django_db
def test_whitespace_only_question_returns_400(api_client):
    response = api_client.post(
        "/api/questions/",
        {"text": "   "},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
@patch("questions.views.process_question_task.apply_async")
def test_question_at_limit_is_accepted(mock_apply_async, api_client):
    response = api_client.post(
        "/api/questions/",
        {"text": "a" * QUESTION_MAX_LENGTH},
        format="json",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED


@pytest.mark.django_db
def test_list_is_paginated_oldest_first(api_client):
    questions = Question.objects.bulk_create(
        Question(text=f"Question {i}") for i in range(25)
    )

    first = api_client.get("/api/questions/").json()

    assert first["count"] == 25
    assert len(first["results"]) == 20
    assert [q["id"] for q in first["results"]] == [q.id for q in questions[:20]]
    assert first["previous"] is None
    assert first["next"] is not None

    second = api_client.get(first["next"]).json()

    assert [q["id"] for q in second["results"]] == [q.id for q in questions[20:]]
    assert second["next"] is None
    # No question appears on both pages.
    first_ids = {q["id"] for q in first["results"]}
    assert first_ids.isdisjoint(q["id"] for q in second["results"])


@pytest.mark.django_db
def test_page_size_is_capped(api_client):
    Question.objects.bulk_create(Question(text=f"Question {i}") for i in range(105))

    response = api_client.get("/api/questions/?page_size=1000")

    assert len(response.json()["results"]) == 100


@pytest.mark.django_db
def test_list_does_not_query_per_question(
    api_client,
    django_assert_max_num_queries,
):
    for i in range(10):
        question = Question.objects.create(text=f"Question {i}")
        SourceDocument.objects.create(
            question=question,
            title=f"Doc {i}",
            url="https://en.wikipedia.org/wiki/Doc",
            content="content",
        )

    # count + page + one prefetch for all source documents: no N+1.
    with django_assert_max_num_queries(3):
        response = api_client.get("/api/questions/")

    assert len(response.json()["results"]) == 10


@pytest.mark.django_db
def test_validation_error_has_consistent_shape(api_client):
    response = api_client.post("/api/questions/", {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "The request data is invalid.",
            "details": {"text": ["This field is required."]},
        }
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "url", "expected_status", "expected_code"),
    [
        ("get", "/api/questions/999999/", 404, "not_found"),
        ("delete", "/api/questions/1/", 405, "method_not_allowed"),
        ("get", "/api/questions/?page=999", 404, "not_found"),
    ],
)
def test_other_errors_have_consistent_shape(
    api_client, method, url, expected_status, expected_code
):
    response = getattr(api_client, method)(url)

    assert response.status_code == expected_status
    error = response.json()["error"]
    assert error["code"] == expected_code
    assert error["message"]
    assert error["details"] is None


@pytest.mark.django_db
def test_malformed_json_returns_parse_error(api_client):
    response = api_client.post(
        "/api/questions/",
        data="{bad json",
        content_type="application/json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "parse_error"


@pytest.mark.django_db
@patch("questions.views.process_question_task.apply_async")
@patch.object(QuestionCreateThrottle, "THROTTLE_RATES", {"question_create": "2/minute"})
def test_question_creation_is_throttled(mock_apply_async, api_client):
    for _ in range(2):
        response = api_client.post(
            "/api/questions/", {"text": "What is Django?"}, format="json"
        )
        assert response.status_code == status.HTTP_202_ACCEPTED

    response = api_client.post(
        "/api/questions/", {"text": "What is Django?"}, format="json"
    )

    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert response.json()["error"]["code"] == "throttled"
    assert "Retry-After" in response.headers
    # Polling is never throttled.
    assert api_client.get("/api/questions/").status_code == status.HTTP_200_OK
