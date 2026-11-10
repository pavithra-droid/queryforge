import httpx

from questions.models import SourceDocument
from questions.providers.base import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate_answer(
        self,
        question: str,
        documents: list[SourceDocument],
    ) -> str:
        if not documents:
            return "I couldn't find relevant information to answer your question."

        context = "\n\n".join(
            f"Title: {document.title}\nContent: {document.content}"
            for document in documents
        )

        response = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful assistant. "
                            "Answer using ONLY the supplied context. "
                            "If the answer is not present in the context, "
                            "say you don't know. Do not invent information."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (f"Context:\n{context}\n\nQuestion: {question}"),
                    },
                ],
            },
            timeout=self.timeout,
        )

        response.raise_for_status()

        answer = response.json().get("message", {}).get("content")

        if not answer:
            raise ValueError("Ollama returned an empty answer.")

        return answer
