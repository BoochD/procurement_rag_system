import os

from openai import OpenAI
from langchain_openai import ChatOpenAI
from pydantic import SecretStr


OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.hydraai.ru/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
OPENAI_NANO_MODEL = os.getenv("OPENAI_NANO_MODEL", "gpt-5-mini")
OPENAI_VLM_MODEL = os.getenv("OPENAI_VLM_MODEL", "gpt-5.4-mini")
OPENAI_FAST_MODEL = os.getenv("OPENAI_FAST_MODEL", "gemini-3.1-flash-lite")
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"


def _get_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        try:
            from dotenv import load_dotenv
            from pathlib import Path
            load_dotenv(Path("web/.env"))
            load_dotenv(Path(".env"))
        except Exception:
            pass
        api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment variables.")
    return api_key


def get_chatGPT_client() -> OpenAI:
    api_key = _get_api_key()
    return OpenAI(
        api_key=api_key,
        base_url=OPENAI_BASE_URL,
    )


def get_langchain_openai_chat_model(model_name: str | None = None) -> ChatOpenAI:
    api_key = _get_api_key()
    selected_model = model_name or OPENAI_MODEL
    return ChatOpenAI(
        api_key=SecretStr(api_key),
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
