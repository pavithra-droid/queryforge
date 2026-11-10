import pytest
from django.core.cache import cache

from questions.models import Question


@pytest.fixture
def question(db):
    return Question.objects.create(
        text="What is Django?",
    )


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    # Throttle counters live in the cache; keep tests independent.
    cache.clear()
    yield
    cache.clear()
