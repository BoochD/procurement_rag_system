import os

from openai import OpenAI
from langchain_openai import ChatOpenAI
from pydantic import SecretStr


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-hydra-ai-4qkTBoAnIGQ_0RmSjib22YAGWiejpGBzg-Aqt2bFaEVE0-rH.vEOxLFyn1lho12X")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.hydraai.ru/v1")
# Model selection is intentionally code-controlled to prevent stale environment
# variables from silently switching production to a different model.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
OPENAI_NANO_MODEL = os.getenv("OPENAI_NANO_MODEL", "gpt-5.4-nano")
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"


def get_chatGPT_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    return OpenAI(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
    )


def get_langchain_openai_chat_model(model_name: str | None = None) -> ChatOpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    selected_model = model_name or OPENAI_MODEL
    return ChatOpenAI(
        api_key=SecretStr(OPENAI_API_KEY),
        base_url=OPENAI_BASE_URL,
        model=selected_model,
        max_tokens=12000,
    )


def get_nano_chat_model() -> ChatOpenAI:
    return get_langchain_openai_chat_model(model_name=OPENAI_NANO_MODEL)


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
