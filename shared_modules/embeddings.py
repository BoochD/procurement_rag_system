from langchain_openai import OpenAIEmbeddings
from pydantic import SecretStr

from .llm_models import (
    _get_api_key,
    OPENAI_BASE_URL,
    OPENAI_EMBEDDING_MODEL,
)


def get_openai_embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        api_key=SecretStr(_get_api_key()),
        base_url=OPENAI_BASE_URL,
        model=OPENAI_EMBEDDING_MODEL,
    )


def get_embeddings() -> OpenAIEmbeddings:
    return get_openai_embeddings()
