from django.conf import settings

from questions.providers.offline_provider import OfflineLLMProvider
from questions.providers.ollama_provider import OllamaProvider


def get_llm_provider():
    provider = settings.LLM_PROVIDER.lower()

    if provider == "offline":
        return OfflineLLMProvider()

    if provider == "ollama":
        return OllamaProvider(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.OLLAMA_MODEL,
            timeout=settings.OLLAMA_TIMEOUT,
        )

    raise ValueError(f"Unsupported LLM provider: {provider}")


def generate_answer(question, documents):
    provider = get_llm_provider()
    return provider.generate_answer(question, documents)
