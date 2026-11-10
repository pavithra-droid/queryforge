import pytest

from questions.models import SourceDocument
from questions.services.document_service import save_source_documents


@pytest.mark.django_db
def test_save_source_documents_creates_documents(question):
    documents = [
        {
            "title": "Django",
            "url": "https://example.com/django",
            "content": "Django is a Python web framework.",
        },
        {
            "title": "Python",
            "url": "https://example.com/python",
            "content": "Python is a programming language.",
        },
    ]

    result = save_source_documents(question, documents)

    assert len(result) == 2
    assert SourceDocument.objects.filter(question=question).count() == 2


@pytest.mark.django_db
def test_save_source_documents_replaces_existing_documents(question):
    SourceDocument.objects.create(
        question=question,
        title="Old document",
        url="https://example.com/old",
        content="Old content",
    )

    documents = [
        {
            "title": "New document",
            "url": "https://example.com/new",
            "content": "New content",
        }
    ]

    save_source_documents(question, documents)

    saved_documents = SourceDocument.objects.filter(question=question)

    assert saved_documents.count() == 1
    assert saved_documents.first().title == "New document"


@pytest.mark.django_db
def test_save_source_documents_preserves_existing_documents_when_empty(question):
    existing_document = SourceDocument.objects.create(
        question=question,
        title="Existing document",
        url="https://example.com/existing",
        content="Existing content",
    )

    result = save_source_documents(question, [])

    assert result == []
    assert SourceDocument.objects.filter(
        question=question,
        id=existing_document.id,
    ).exists()
