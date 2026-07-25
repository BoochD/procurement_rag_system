from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import contextmanager
from typing import TypeVar

from pydantic import BaseModel

from summary_model.classification.document_classifier import ClassificationDecision
from summary_model.domain.models import DocumentIR, DocumentType
from .prompts import CLASSIFIER_PROMPT, COMMON_EXTRACTION_PROMPT
from .table_projection import render_table_for_llm


T = TypeVar("T", bound=BaseModel)


class EmptyStructuredOutputError(RuntimeError):
    pass


def _is_non_retryable(error: Exception) -> bool:
    if isinstance(error, EmptyStructuredOutputError):
        return True
    message = str(error).lower()
    if "input_value=none" in message and "input_type=nonetype" in message:
        return True
    return "error code: 400" in message and "invalid_request_error" in message


def _structured_error_message(error: Exception) -> str:
    if isinstance(error, EmptyStructuredOutputError):
        return str(error)
    message = str(error)
    lowered = message.lower()
    if "input_value=none" in lowered and "input_type=nonetype" in lowered:
        return "LLM вернула пустой structured output (None). Использован deterministic fallback."
    return message


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
        model_name: str | None = None,
        semaphore: asyncio.Semaphore | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        if model is None:
            from shared_modules.llm_models import get_langchain_openai_chat_model

            model = get_langchain_openai_chat_model(model_name=model_name)
        self.model = model
        self.semaphore = semaphore or asyncio.Semaphore(3)
        self.timeout_seconds = timeout_seconds
        self.calls = 0
        self.retries = 0
        self.errors: list[str] = []
        self.retry_reasons: list[str] = []
        self.input_characters = 0
        self.duration_seconds = 0.0
        self.attempts: list[dict[str, object]] = []
        self._call_context: dict[str, object] = {}

    def metrics(self) -> dict[str, object]:
        output_characters = sum(
            int(attempt.get("output_characters") or 0)
            for attempt in self.attempts
        )
        return {
            "calls": self.calls,
            "retries": self.retries,
            "errors": list(self.errors),
            "retry_reasons": list(self.retry_reasons),
            "input_characters": self.input_characters,
            "estimated_input_tokens": _estimate_tokens(self.input_characters),
            "output_characters": output_characters,
            "estimated_output_tokens": _estimate_tokens(output_characters) if output_characters else 0,
            "estimated_total_tokens": (
                _estimate_tokens(self.input_characters)
                + (_estimate_tokens(output_characters) if output_characters else 0)
            ),
            "duration_seconds": round(self.duration_seconds, 3),
            "model": getattr(self.model, "model_name", None),
            "reasoning_effort": getattr(self.model, "reasoning_effort", None),
            "max_tokens": getattr(self.model, "max_tokens", None),
            "attempts": list(self.attempts),
        }

    @contextmanager
    def call_context(self, **values: object):
        previous = dict(self._call_context)
        self._call_context = {**previous, **{key: value for key, value in values.items() if value is not None}}
        try:
            yield
        finally:
            self._call_context = previous

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
            attempt_started = time.perf_counter()
            result = structured.invoke(prompt)
            if result is None:
                raise EmptyStructuredOutputError(
                    "LLM вернула пустой structured output (None). Использован deterministic fallback."
                )
            validated = schema.model_validate(result)
            self._record_attempt(
                schema=schema,
                prompt=prompt,
                output=_model_output_text(validated),
                duration_seconds=time.perf_counter() - attempt_started,
                success=True,
            )
            return validated, None
        except Exception as first_error:
            self._record_attempt(
                schema=schema,
                prompt=prompt,
                duration_seconds=time.perf_counter() - started,
                success=False,
                error=first_error,
            )
            if _is_non_retryable(first_error):
                message = f"Structured extraction failed: {_structured_error_message(first_error)}"
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
                attempt_started = time.perf_counter()
                result = structured.invoke(retry_prompt)
                if result is None:
                    raise EmptyStructuredOutputError(
                        "LLM вернула пустой structured output (None). Использован deterministic fallback."
                    )
                validated = schema.model_validate(result)
                self._record_attempt(
                    schema=schema,
                    prompt=retry_prompt,
                    output=_model_output_text(validated),
                    duration_seconds=time.perf_counter() - attempt_started,
                    success=True,
                    is_retry=True,
                )
                return validated, None
            except Exception as second_error:
                self._record_attempt(
                    schema=schema,
                    prompt=retry_prompt,
                    duration_seconds=time.perf_counter() - started,
                    success=False,
                    error=second_error,
                    is_retry=True,
                )
                message = f"Structured extraction failed after retry: {_structured_error_message(second_error)}"
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
                        attempt_started = time.perf_counter()
                        result = await asyncio.wait_for(
                            structured.ainvoke(request_prompt),
                            timeout=self.timeout_seconds,
                        )
                    if result is None:
                        raise EmptyStructuredOutputError(
                            "LLM вернула пустой structured output (None). Использован deterministic fallback."
                        )
                    validated = schema.model_validate(result)
                    self._record_attempt(
                        schema=schema,
                        prompt=request_prompt,
                        output=_model_output_text(validated),
                        duration_seconds=time.perf_counter() - attempt_started,
                        success=True,
                        is_retry=bool(attempt),
                    )
                    return validated, None
                except Exception as error:
                    self._record_attempt(
                        schema=schema,
                        prompt=request_prompt,
                        duration_seconds=time.perf_counter() - started,
                        success=False,
                        error=error,
                        is_retry=bool(attempt),
                    )
                    first_error = error
                    if _is_non_retryable(error):
                        message = f"Structured extraction failed: {_structured_error_message(error)}"
                        self.errors.append(message)
                        return None, message
                    if attempt == 0:
                        self.retry_reasons.append(str(error)[:500])
            message = f"Structured extraction failed after retry: {_structured_error_message(first_error)}"
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

    def _record_attempt(
        self,
        *,
        schema: type[BaseModel],
        prompt: str,
        output: str | None = None,
        duration_seconds: float,
        success: bool,
        error: Exception | None = None,
        is_retry: bool = False,
    ) -> None:
        payload_file_name = self._call_context.get("file_name") or _file_name_from_prompt(prompt)
        payload_document_type = self._call_context.get("document_type") or _document_type_from_prompt(prompt)
        record: dict[str, object] = {
            "schema": schema.__name__,
            "file_name": payload_file_name,
            "document_type": str(payload_document_type) if payload_document_type else None,
            "is_retry": is_retry,
            "success": success,
            "input_characters": len(prompt),
            "estimated_input_tokens": _estimate_tokens(len(prompt)),
            "duration_seconds": round(duration_seconds, 3),
        }
        if output is not None:
            record["output_characters"] = len(output)
            record["estimated_output_tokens"] = _estimate_tokens(len(output))
        if error is not None:
            record["error"] = _structured_error_message(error)[:500]
        self.attempts.append(record)


def _estimate_tokens(characters: int) -> int:
    return max(1, round(characters / 4))


def _file_name_from_prompt(prompt: str) -> str | None:
    for pattern in (r'"file_name"\s*:\s*"([^"]+)"', r"file_name=([^\n]+)"):
        match = re.search(pattern, prompt)
        if match:
            return match.group(1)
    return None


def _document_type_from_prompt(prompt: str) -> str | None:
    match = re.search(r'"document_type"\s*:\s*"([^"]+)"', prompt)
    if match:
        return match.group(1)
    try:
        payload = prompt.split("DOCUMENT:\n", 1)[1]
        data = json.loads(payload)
    except (IndexError, json.JSONDecodeError):
        return None
    document = data.get("document") if isinstance(data, dict) else None
    return document.get("document_type") if isinstance(document, dict) else None


def _model_output_text(value: BaseModel) -> str:
    return value.model_dump_json(exclude_none=True)


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
