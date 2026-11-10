from datetime import timedelta

import httpx
from celery import Task, shared_task
from django.conf import settings
from django.utils import timezone

from questions.models import Question
from questions.services.process_service import process_question


class RetryableHTTPError(Exception):
    """An HTTP error that should be retried."""


class QuestionProcessingTask(Task):
    """Handle question processing failures after all retries."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        question_id = args[0]

        Question.objects.filter(id=question_id).update(
            status=Question.Status.FAILED,
            error_message=(
                "We couldn't process your question. Please try again later."
            ),
            updated_at=timezone.now(),
        )

        super().on_failure(exc, task_id, args, kwargs, einfo)


@shared_task(
    bind=True,
    base=QuestionProcessingTask,
    max_retries=3,
    autoretry_for=(
        httpx.RequestError,
        RetryableHTTPError,
    ),
    retry_backoff=2,
    retry_backoff_max=30,
    retry_jitter=True,
)
def process_question_task(self, question_id: int):
    try:
        question = process_question(question_id)

        return {
            "question_id": question.id,
            "status": question.status,
        }

    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code

        if status_code == 429 or status_code >= 500:
            raise RetryableHTTPError(f"Retryable HTTP error: {status_code}") from exc

        raise


@shared_task
def recover_stale_questions():
    stale_deadline = timezone.now() - timedelta(
        seconds=settings.QUESTION_PROCESSING_TIMEOUT
    )

    stale_question_ids = Question.objects.filter(
        status=Question.Status.PROCESSING,
        updated_at__lt=stale_deadline,
    ).values_list("id", flat=True)

    recovered_count = 0

    for question_id in stale_question_ids:
        process_question_task.delay(question_id)
        recovered_count += 1

    return recovered_count
