from typing import List

from langchain_core.output_parsers import StrOutputParser

from shared_modules.llm_models import get_langchain_openai_chat_model

from .prompts import prompt_smart


def process_smart_points(
        plan_points: str,
        contract_points: str,
        OOZ_points: str,
        zapiska_points: str,
        ONMCK_points: str,
        available_documents: str,
        ) -> str:
    llm = get_langchain_openai_chat_model()
    chain = prompt_smart | llm | StrOutputParser()


    return chain.invoke({
        "plan_points": plan_points,
        "contract_points": contract_points,
        "OOZ_points": OOZ_points,
        "zapiska_points": zapiska_points,
        "ONMCK_points": ONMCK_points,
        "available_documents": available_documents,
    }).strip()

