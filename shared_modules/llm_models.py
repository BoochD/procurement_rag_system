import os

from openai import OpenAI
from langchain_openai import ChatOpenAI
from pydantic import SecretStr


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-uNPNElzXmM4gtuLf1Gjst1MPNxPqoPke")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.proxyapi.ru/openai/v1")
# Model selection is intentionally code-controlled to prevent stale environment
# variables from silently switching production to a different model.
OPENAI_MODEL = "gpt-5.4-nano"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"


def get_chatGPT_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    return OpenAI(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
    )


def get_langchain_openai_chat_model() -> ChatOpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    return ChatOpenAI(
        api_key=SecretStr(OPENAI_API_KEY),
        base_url=OPENAI_BASE_URL,
        model=OPENAI_MODEL,
        max_tokens=12000,
    )


def get_openai_embedding(text: str) -> list[float]:
    """Return an embedding vector using the configured OpenAI-compatible client."""
    client = get_chatGPT_client()
    response = client.embeddings.create(
        model=OPENAI_EMBEDDING_MODEL,
        input=text,
    )
    return list(response.data[0].embedding)


def get_openai_embeddings(texts: list[str]) -> list[list[float]]:
    """Return embedding vectors for a batch of texts."""
    if not texts:
        return []
    client = get_chatGPT_client()
    response = client.embeddings.create(
        model=OPENAI_EMBEDDING_MODEL,
        input=texts,
    )
    return [list(item.embedding) for item in response.data]
