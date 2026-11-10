import pytest

from questions.models import Question, SourceDocument
from questions.services.retrieval_service import retrieve_relevant_documents


def _documents(question, *items):
    # Insertion order mirrors Wikipedia's search ranking.
    SourceDocument.objects.bulk_create(
        SourceDocument(
            question=question,
            title=title,
            url=f"https://en.wikipedia.org/wiki/{title}",
            content=content,
        )
        for title, content in items
    )


@pytest.mark.django_db
def test_filler_words_do_not_prevent_a_match():
    # Regression: an AND query required "tell" to appear in the document.
    question = Question.objects.create(text="Can you tell me what Redis is?")
    _documents(
        question,
        ("Redis", "Redis is an in-memory key-value database used as a cache."),
    )

    result = retrieve_relevant_documents(question.text, question.id)

    assert [doc.title for doc in result] == ["Redis"]


@pytest.mark.django_db
def test_main_article_beats_short_stub_and_stemming_lookalike():
    # Regression: length normalisation favoured stubs, and the English
    # stemmer turned "redis" into "redi", matching the article "Redi".
    question = Question.objects.create(text="what is redis and what is it used for")
    _documents(
        question,
        (
            "Redis",
            "Redis is an in-memory key-value database, used as a distributed "
            "cache and message broker. Redis offers low-latency reads. "
            "Redis is used by many companies and is often used for sessions.",
        ),
        ("Redi", "Redi is a surname."),
    )

    result = retrieve_relevant_documents(question.text, question.id)

    assert result[0].title == "Redis"


@pytest.mark.django_db
def test_falls_back_to_wikipedia_order_when_nothing_matches():
    question = Question.objects.create(text="zzz qqq")
    _documents(question, ("First", "alpha"), ("Second", "beta"))

    result = retrieve_relevant_documents(question.text, question.id)

    assert [doc.title for doc in result] == ["First", "Second"]


@pytest.mark.django_db
def test_only_returns_documents_for_the_given_question():
    question = Question.objects.create(text="What is Django?")
    other = Question.objects.create(text="What is Django?")
    _documents(other, ("Django", "Django is a web framework."))

    assert retrieve_relevant_documents(question.text, question.id) == []
