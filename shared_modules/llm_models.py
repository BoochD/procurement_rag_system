import os

from gigachat import GigaChat
from openai import OpenAI
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

try:
    from langchain_gigachat.chat_models import GigaChat as LangchainGigaChat
except ModuleNotFoundError:
    LangchainGigaChat = None


AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "your-gigachat-auth-key")
GIGACHAT_TIMEOUT = int(os.getenv("GIGACHAT_TIMEOUT", "180"))
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Max")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.proxyapi.ru/openai/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.3-chat-latest")


def get_gigachat_client() -> GigaChat:
    return GigaChat(
        verify_ssl_certs=False,
        credentials=AUTH_KEY,
        model=GIGACHAT_MODEL,
        timeout=GIGACHAT_TIMEOUT,
    )


def get_chatGPT_client() -> OpenAI:
    client = OpenAI(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
    )
    return client


def get_langchain_openai_chat_model() -> ChatOpenAI:
    return ChatOpenAI(
        api_key=SecretStr(OPENAI_API_KEY),
        base_url=OPENAI_BASE_URL,
        model=OPENAI_MODEL,
    )


def get_langchain_gigachat_model():
    if LangchainGigaChat is None:
        raise ModuleNotFoundError(
            "langchain_gigachat is not installed. Install the package from requirements "
            "before using GigaChat with LangChain."
        )

    return LangchainGigaChat(
        credentials=AUTH_KEY,
        model=GIGACHAT_MODEL,
        verify_ssl_certs=False,
        timeout=GIGACHAT_TIMEOUT,
    )


def get_langchain_chat_model(provider: str = "openai"):
    if provider == "gigachat":
        return get_langchain_gigachat_model()
    if provider == "openai":
        return get_langchain_openai_chat_model()
    raise ValueError(f"Unsupported LLM provider: {provider}")
