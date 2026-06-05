from typing import List, Optional

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.retrievers import BM25Retriever as LangchainBM25Retriever
from langchain_community.vectorstores import FAISS


splitter = RecursiveCharacterTextSplitter(
    chunk_size=300,
    chunk_overlap=100,
)


def _split_texts_to_documents(
    texts: List[str],
    sources: Optional[List[str]] = None,
) -> List[Document]:
    if sources is None:
        sources = [f"source_{i}" for i in range(len(texts))]

    documents: List[Document] = []
    for text, source in zip(texts, sources):
        chunks = splitter.split_text(text)
        documents.extend(
            Document(page_content=chunk, metadata={"source": source})
            for chunk in chunks
        )

    return documents


class Retriever:
    def __init__(self, embeddings):
        self.embeddings = embeddings

    def create_retriever(
        self,
        texts: List[str],
        n: int = 5,
        sources: Optional[List[str]] = None,
    ):
        documents = _split_texts_to_documents(texts, sources=sources)
        all_chunks = [doc.page_content for doc in documents]
        all_metadatas = [doc.metadata for doc in documents]

        faiss_index = FAISS.from_texts(
            all_chunks,
            self.embeddings,
            metadatas=all_metadatas,
        )

        return faiss_index.as_retriever(search_kwargs={"k": n})


class BM25TextRetriever:
    def create_retriever(
        self,
        texts: List[str],
        n: int = 5,
        sources: Optional[List[str]] = None,
    ):
        documents = _split_texts_to_documents(texts, sources=sources)
        retriever = LangchainBM25Retriever.from_documents(documents)
        retriever.k = n
        return retriever
