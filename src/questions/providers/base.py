from abc import ABC, abstractmethod

from questions.models import SourceDocument


class LLMProvider(ABC):
    @abstractmethod
    def generate_answer(
        self,
        question: str,
        documents: list[SourceDocument],
    ) -> str:
        pass
