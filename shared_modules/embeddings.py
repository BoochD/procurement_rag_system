from langchain_openai import OpenAIEmbeddings
from langchain_gigachat.embeddings import GigaChatEmbeddings
from pydantic import SecretStr

from .llm_models import AUTH_KEY, OPENAI_API_KEY, OPENAI_BASE_URL

OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"
GIGACHAT_EMBEDDING_MODEL = "Embeddings"


def get_openai_embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        api_key=SecretStr(OPENAI_API_KEY),
        base_url=OPENAI_BASE_URL,
        model=OPENAI_EMBEDDING_MODEL,
    )


def get_gigachat_embeddings() -> GigaChatEmbeddings:
    return GigaChatEmbeddings(
        credentials=AUTH_KEY,
        verify_ssl_certs=False,
        model=GIGACHAT_EMBEDDING_MODEL,
    )


def get_embeddings(provider: str = "gigachat"):
    if provider == "gigachat":
        return get_gigachat_embeddings()
    if provider == "openai":
        return get_openai_embeddings()
    raise ValueError(f"Unsupported embeddings provider: {provider}")
