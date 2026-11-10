import operator
from functools import reduce

from django.contrib.postgres.search import (
    SearchQuery,
    SearchRank,
    SearchVector,
)

from questions.models import SourceDocument
from questions.services.text_utils import extract_keywords


def retrieve_relevant_documents(
    question: str,
    question_id: int,
    limit: int = 3,
) -> list[SourceDocument]:
    """
    Re-rank a question's source documents with PostgreSQL full-text search.

    Keywords are OR-ed together: a document matching any keyword is a
    candidate, and SearchRank scores documents matching more of them (and
    matching in the title) higher. Ties keep Wikipedia's order. If nothing
    matches, fall back to
    Wikipedia's ranking, preserved by insertion order (`id`).
    """
    documents = SourceDocument.objects.filter(question_id=question_id)
    keywords = extract_keywords(question)

    if not keywords:
        return list(documents.order_by("id")[:limit])

    # Titles are names ("Redis", "Django (web framework)"): match them
    # unstemmed, otherwise the English stemmer turns "redis" into "redi" and
    # the unrelated article "Redi" wins. Body text benefits from stemming.
    search_vector = SearchVector("title", weight="A", config="simple") + SearchVector(
        "content", weight="B", config="english"
    )

    search_query = reduce(
        operator.or_,
        (
            SearchQuery(keyword, config=config)
            for keyword in keywords
            for config in ("simple", "english")
        ),
    )

    ranked = list(
        documents.annotate(
            # normalization=0: no document-length penalty. Length
            # normalisation made short stub articles outrank the main one.
            rank=SearchRank(search_vector, search_query, normalization=0),
        )
        .filter(rank__gt=0)
        .order_by("-rank", "id")[:limit]
    )

    return ranked or list(documents.order_by("id")[:limit])
