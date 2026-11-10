from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from questions.models import Question
from questions.services.answer_service import generate_answer
from questions.services.document_service import save_source_documents
from questions.services.retrieval_service import retrieve_relevant_documents
from questions.services.wikipedia_service import search_wikipedia


def process_question(question_id: int) -> Question:
    with transaction.atomic():
        question = Question.objects.select_for_update().get(id=question_id)

        if question.status == Question.Status.COMPLETED:
            return question

        if question.status == Question.Status.PROCESSING:
            timeout = timedelta(seconds=settings.QUESTION_PROCESSING_TIMEOUT)

            processing_deadline = timezone.now() - timeout

            if question.updated_at > processing_deadline:
                return question

        question.status = Question.Status.PROCESSING
        question.save(update_fields=["status", "updated_at"])

    try:
        documents = search_wikipedia(question.text)

        save_source_documents(question, documents)

        relevant_documents = retrieve_relevant_documents(
            question=question.text,
            question_id=question.id,
            limit=3,
        )

        answer = generate_answer(
            question=question.text,
            documents=relevant_documents,
        )
    except Exception:
        # Release the claim so a Celery retry can pick the question up
        # immediately. Without this, the retry sees a fresh PROCESSING
        # status, assumes another worker owns it, and returns doing nothing.
        Question.objects.filter(
            id=question.id,
            status=Question.Status.PROCESSING,
        ).update(status=Question.Status.PENDING, updated_at=timezone.now())
        raise

    question.answer = answer
    question.status = Question.Status.COMPLETED
    question.error_message = ""

    question.save(
        update_fields=[
            "answer",
            "status",
            "error_message",
            "updated_at",
        ]
    )

    return question
