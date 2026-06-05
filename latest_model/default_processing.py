from typing import List

from langchain_core.output_parsers import StrOutputParser

from shared_modules.llm_models import get_langchain_chat_model

from .prompts import prompt_default


def process_default_points(plan_points: List[str], contract_full_text: str) -> str:
    llm = get_langchain_chat_model()
    chain = prompt_default | llm | StrOutputParser()

    plan_points_text = "\n".join(
        f"{i}. {point}" for i, point in enumerate(plan_points, start=1)
    )

    return chain.invoke({
        "plan_points": plan_points_text,
        "contract_full_text": contract_full_text,
    }).strip()

