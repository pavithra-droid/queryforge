from questions.models import SourceDocument
from questions.providers.base import LLMProvider
from questions.services.text_utils import (
    extract_keywords,
    split_sentences,
    tokenize,
)

NO_ANSWER = "I couldn't find relevant information to answer your question."


class OfflineLLMProvider(LLMProvider):
    """
    Deterministic, key-free stand-in for an LLM.

    Extractive answer: takes the highest-ranked document that mentions any
    question keyword, picks its sentences sharing the most keywords with the
    question, and cites the article. Answering from a single document keeps
    the reply coherent instead of stitching sentences from unrelated pages.
    """

    max_sentences = 3

    def generate_answer(
        self,
        question: str,
        documents: list[SourceDocument],
    ) -> str:
        keywords = set(extract_keywords(question))

        for document in documents:
            scored = [
                (len(keywords & set(tokenize(sentence))), position, sentence)
                for position, sentence in enumerate(split_sentences(document.content))
            ]
            scored = [item for item in scored if item[0] > 0]

            if not scored:
                continue

            best = sorted(scored, key=lambda item: (-item[0], item[1]))
            best = best[: self.max_sentences]

            # Present the chosen sentences in reading order.
            best.sort(key=lambda item: item[1])

            answer = " ".join(sentence for _, _, sentence in best)

            return f"{answer}\n\nSource: {document.title} ({document.url})"

        return NO_ANSWER
