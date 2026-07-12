from __future__ import annotations

import asyncio
import time
from typing import TypeVar

from pydantic import BaseModel

from summary_model.classification.document_classifier import ClassificationDecision
from summary_model.domain.models import DocumentIR, DocumentType
from .prompts import CLASSIFIER_PROMPT, COMMON_EXTRACTION_PROMPT
from .table_projection import render_table_for_llm


T = TypeVar("T", bound=BaseModel)


def _is_non_retryable(error: Exception) -> bool:
    message = str(error).lower()
    return "error code: 400" in message and "invalid_request_error" in message


def _ir_preview(ir: DocumentIR, max_blocks: int = 20) -> str:
    lines = [f"document_id={ir.document_id}", f"file_name={ir.file_name}"]
    for block in ir.blocks[:max_blocks]:
        if block.text:
            lines.append(f"[{block.block_id}] {block.text}")
        elif block.table:
            lines.append(
                f"[{block.block_id}] TABLE title={block.table.title!r} "
                f"columns={block.table.header_labels()!r}"
            )
    return "\n".join(lines)


class StructuredLLMClient:
    def __init__(
        self,
        model=None,
        *,
        semaphore: asyncio.Semaphore | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        if model is None:
            from shared_modules.llm_models import get_langchain_openai_chat_model

            model = get_langchain_openai_chat_model()
        self.model = model
        self.semaphore = semaphore or asyncio.Semaphore(3)
        self.timeout_seconds = timeout_seconds
        self.calls = 0
        self.retries = 0
        self.errors: list[str] = []
        self.retry_reasons: list[str] = []
        self.input_characters = 0
        self.duration_seconds = 0.0

    def metrics(self) -> dict[str, object]:
        return {
            "calls": self.calls,
            "retries": self.retries,
            "errors": list(self.errors),
            "retry_reasons": list(self.retry_reasons),
            "input_characters": self.input_characters,
            "duration_seconds": round(self.duration_seconds, 3),
            "model": getattr(self.model, "model_name", None),
            "reasoning_effort": getattr(self.model, "reasoning_effort", None),
            "max_tokens": getattr(self.model, "max_tokens", None),
        }

    def extract(
        self,
        schema: type[T],
        system_prompt: str,
        payload: str,
    ) -> tuple[T | None, str | None]:
        structured = self.model.with_structured_output(
            schema,
            method="function_calling",
        )
        prompt = f"{COMMON_EXTRACTION_PROMPT}\n\n{system_prompt}\n\nDOCUMENT:\n{payload}"
        started = time.perf_counter()
        try:
            self.calls += 1
            self.input_characters += len(prompt)
            result = structured.invoke(prompt)
            return schema.model_validate(result), None
        except Exception as first_error:
            if _is_non_retryable(first_error):
                message = f"Structured extraction failed: {first_error}"
                self.errors.append(message)
                return None, message
            retry_prompt = (
                prompt
                + "\n\nПредыдущий ответ не прошёл schema validation. "
                + f"Ошибка: {first_error}. Исправь ответ, не добавляя фактов."
            )
            self.retries += 1
            self.retry_reasons.append(str(first_error)[:500])
            try:
                self.calls += 1
                self.input_characters += len(retry_prompt)
                result = structured.invoke(retry_prompt)
                return schema.model_validate(result), None
            except Exception as second_error:
                message = f"Structured extraction failed after retry: {second_error}"
                self.errors.append(message)
                return None, message
        finally:
            self.duration_seconds += time.perf_counter() - started

    async def aextract(
        self,
        schema: type[T],
        system_prompt: str,
        payload: str,
    ) -> tuple[T | None, str | None]:
        structured = self.model.with_structured_output(
            schema,
            method="function_calling",
        )
        prompt = f"{COMMON_EXTRACTION_PROMPT}\n\n{system_prompt}\n\nDOCUMENT:\n{payload}"
        started = time.perf_counter()
        first_error: Exception | None = None
        try:
            for attempt in range(2):
                if attempt:
                    self.retries += 1
                    delay = 2.0 if first_error and "429" in str(first_error) else 0.5
                    await asyncio.sleep(delay)
                request_prompt = prompt
                if first_error is not None:
                    request_prompt += (
                        "\n\nПредыдущий ответ не прошёл schema validation. "
                        f"Ошибка: {first_error}. Исправь ответ, не добавляя фактов."
                    )
                try:
                    async with self.semaphore:
                        self.calls += 1
                        self.input_characters += len(request_prompt)
                        result = await asyncio.wait_for(
                            structured.ainvoke(request_prompt),
                            timeout=self.timeout_seconds,
                        )
                    return schema.model_validate(result), None
                except Exception as error:
                    first_error = error
                    if _is_non_retryable(error):
                        message = f"Structured extraction failed: {error}"
                        self.errors.append(message)
                        return None, message
                    if attempt == 0:
                        self.retry_reasons.append(str(error)[:500])
            message = f"Structured extraction failed after retry: {first_error}"
            self.errors.append(message)
            return None, message
        finally:
            self.duration_seconds += time.perf_counter() - started

    def classify(
        self,
        ir: DocumentIR,
        type_hint: DocumentType | None,
    ) -> tuple[ClassificationDecision | None, str | None]:
        payload = (
            f"type_hint={type_hint.value if type_hint else None}\n"
            f"{_ir_preview(ir)}"
        )
        return self.extract(ClassificationDecision, CLASSIFIER_PROMPT, payload)

    async def aclassify(
        self,
        ir: DocumentIR,
        type_hint: DocumentType | None,
    ) -> tuple[ClassificationDecision | None, str | None]:
        payload = (
            f"type_hint={type_hint.value if type_hint else None}\n"
            f"{_ir_preview(ir)}"
        )
        return await self.aextract(ClassificationDecision, CLASSIFIER_PROMPT, payload)


def render_ir_for_llm(
    ir: DocumentIR,
    *,
    include_paragraphs: bool = True,
    include_tables: bool = True,
    max_chars: int = 120_000,
) -> str:
    chunks: list[str] = [f"document_id={ir.document_id}", f"file_name={ir.file_name}"]
    for block in ir.blocks:
        if block.type == "paragraph" and include_paragraphs and block.text:
            chunks.append(f"[BLOCK {block.block_id}]\n{block.text}")
        elif block.type == "table" and include_tables and block.table:
            chunks.append(
                render_table_for_llm(block.table, block_id=block.block_id)
            )
        if sum(len(chunk) for chunk in chunks) >= max_chars:
            chunks.append("[TRUNCATED]")
            break
    return "\n\n".join(chunks)


def render_ir_chunks(
    ir: DocumentIR,
    *,
    include_paragraphs: bool = True,
    include_tables: bool = True,
    max_chars: int = 30_000,
) -> list[str]:
    prefix = f"document_id={ir.document_id}\nfile_name={ir.file_name}\n"
    chunks: list[str] = []
    current = prefix
    for block in ir.blocks:
        rendered = ""
        if block.type == "paragraph" and include_paragraphs and block.text:
            rendered = f"[BLOCK {block.block_id}]\n{block.text}\n\n"
        elif block.type == "table" and include_tables and block.table:
            rendered = (
                render_table_for_llm(block.table, block_id=block.block_id)
                + "\n\n"
            )
        if not rendered:
            continue
        if len(current) > len(prefix) and len(current) + len(rendered) > max_chars:
            chunks.append(current.rstrip())
            current = prefix
        current += rendered
    if len(current) > len(prefix):
        chunks.append(current.rstrip())
    return chunks or [prefix.rstrip()]
