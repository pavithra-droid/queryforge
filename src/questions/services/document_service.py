from django.db import transaction

from questions.models import SourceDocument


def save_source_documents(question, documents):
    """
    Replace the existing source documents for a question
    with the newly retrieved documents.
    """

    if not documents:
        return []

    source_documents = [
        SourceDocument(
            question=question,
            title=document["title"],
            url=document["url"],
            content=document["content"],
        )
        for document in documents
    ]

    with transaction.atomic():
        SourceDocument.objects.filter(question=question).delete()

        return SourceDocument.objects.bulk_create(source_documents)
