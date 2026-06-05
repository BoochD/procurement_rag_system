from operator import itemgetter
from typing import Iterable, List

from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda

from shared_modules.llm_models import get_langchain_chat_model

from .prompts import prompt_rag


def _format_chunks(docs):
    return "\n\n".join(
        f"[source={doc.metadata.get('source', 'unknown')}]\n{doc.page_content}"
        for i, doc in enumerate(docs)
    )


def process_rag_points(retriever, plan_points: List[str]) -> str:
    llm = get_langchain_chat_model()

    rag_chain = (
        {
            "point_index": itemgetter("point_index"),
            "plan_point": itemgetter("plan_point"),
            "context": itemgetter("plan_point") | retriever | RunnableLambda(_format_chunks),
        }
        | prompt_rag
        | llm
        | StrOutputParser()
    )

    answers = []
    for index, point in enumerate(plan_points, start=1):
        answer = rag_chain.invoke({
            "point_index": index,
            "plan_point": point,
        })
        answers.append(answer.strip())

    return "\n\n".join(answers)

