from langchain_openai import OpenAIEmbeddings
from pydantic import SecretStr

from .llm_models import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_EMBEDDING_MODEL,
)


def get_openai_embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        api_key=SecretStr(OPENAI_API_KEY),
        base_url=OPENAI_BASE_URL,
        model=OPENAI_EMBEDDING_MODEL,
    )


def get_embeddings() -> OpenAIEmbeddings:
    return get_openai_embeddings()
