from unittest.mock import patch

import httpx
import pytest
from celery.exceptions import Retry

from questions.models import Question
from questions.tasks import process_question_task


@pytest.mark.django_db
@patch("questions.tasks.process_question")
def test_task_returns_question_status(mock_process, question):
    question.status = Question.Status.COMPLETED
    question.answer = "Django is a web framework."
    question.save()

    mock_process.return_value = question

    result = process_question_task.run(question.id)

    assert result == {
        "question_id": question.id,
        "status": Question.Status.COMPLETED,
    }

    mock_process.assert_called_once_with(question.id)


@pytest.mark.django_db
@patch("questions.tasks.process_question")
def test_task_retries_on_network_error(mock_process, question):
    mock_process.side_effect = httpx.TimeoutException("Simulated timeout")

    with (
        patch.object(
            process_question_task,
            "retry",
            side_effect=Retry(),
        ) as mock_retry,
        pytest.raises(Retry),
    ):
        process_question_task.run(question.id)

    mock_retry.assert_called_once()

    retry_kwargs = mock_retry.call_args.kwargs
    assert isinstance(retry_kwargs["exc"], httpx.TimeoutException)
    assert 0 <= retry_kwargs["countdown"] <= 2


@pytest.mark.django_db
@patch("questions.tasks.process_question")
def test_task_retries_on_http_503(mock_process, question):
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(503, request=request)

    error = httpx.HTTPStatusError(
        "Server error",
        request=request,
        response=response,
    )

    mock_process.side_effect = error

    with (
        patch.object(
            process_question_task,
            "retry",
            side_effect=Retry(),
        ) as mock_retry,
        pytest.raises(Retry),
    ):
        process_question_task.run(question.id)

    mock_retry.assert_called_once()


@pytest.mark.django_db
@patch("questions.tasks.process_question")
def test_task_does_not_retry_on_http_400(mock_process, question):
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(400, request=request)

    error = httpx.HTTPStatusError(
        "Bad request",
        request=request,
        response=response,
    )

    mock_process.side_effect = error

    with pytest.raises(httpx.HTTPStatusError):
        process_question_task.run(question.id)

    mock_process.assert_called_once_with(question.id)


@pytest.mark.django_db
def test_final_failure_marks_question_as_failed(question):
    error = Exception("Simulated processing failure")

    process_question_task.on_failure(
        exc=error,
        task_id="test-task-id",
        args=(question.id,),
        kwargs={},
        einfo=None,
    )

    question.refresh_from_db()

    assert question.status == Question.Status.FAILED
    assert question.error_message == (
        "We couldn't process your question. Please try again later."
    )
