"""Unified registry for procurement reference data.

Supports:
- PP RF No. 1875 local parsed tables
- KTRU live fetch and parsing from zakupki.gov.ru
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date, datetime
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup


OKPD_RE = re.compile(r"^\d{2}(?:\.\d{1,3}){1,4}$")
KTRU_RE = re.compile(r"^\d{2}(?:\.\d{1,3}){1,4}-\d{8}$")
SPACE_RE = re.compile(r"\s+")


class KTRUNotFoundError(Exception):
    """Raised when KTRU card page does not exist."""


@dataclass
class MatchResult:
    found: bool
    source: str
    query_okpd2: str
    matched_okpd2: Optional[str]
    is_parent_match: bool
    checked_candidates: list[str]

    query_name: Optional[str]
    exact_okpd_match: bool
    exact_name_match: bool
    normalized_name_match: bool
    similarity: float

    table_id: Optional[str]
    table_title: Optional[str]
    reference_name: Optional[str]
    position: Optional[str]
    row: Optional[dict[str, Any]]

    message: str


@dataclass
class KTRUMatchResult:
    found: bool
    source: str
    query_ktru: str
    query_name: Optional[str]

    exact_ktru_match: bool
    exact_name_match: bool
    normalized_name_match: bool
    similarity: float

    ktru_code: Optional[str]
    okpd2_code: Optional[str]
    okpd2_name: Optional[str]
    reference_name: Optional[str]
    unit: Optional[str]
    short_description: list[str]

    common_info_url: Optional[str]
    payload: Optional[dict[str, Any]]
    message: str


class ProcurementReferenceRegistry:
    """Load local reference datasets and perform validation lookups."""

    KTRU_BASE_URL = "https://zakupki.gov.ru/epz/ktru/ktruCard"

    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/135.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    def __init__(self, base_dir: Path, sqlite_file: str = "pp1875.sqlite") -> None:
        self.base_dir = self._resolve_base_dir(Path(base_dir))
        self.sqlite_path = self.base_dir / sqlite_file
        self.index_json_path = self.base_dir / "okpd_index.json"
        self._ktru_provider: Any | None = None
        self._ktru_html_cache: dict[str, str] = {}
        self._http = requests.Session()
        self._http.trust_env = os.getenv(
            "KTRU_TRUST_ENV_PROXY",
            "",
        ).strip().casefold() in {"1", "true", "yes"}
        ca_bundle = os.getenv("KTRU_CA_BUNDLE", "").strip()
        verify_tls = os.getenv("KTRU_VERIFY_TLS", "0").strip().casefold()
        self._tls_verify: bool | str = (
            ca_bundle
            if ca_bundle
            else verify_tls not in {"0", "false", "no"}
        )

    @staticmethod
    def _resolve_base_dir(base_dir: Path) -> Path:
        if base_dir.exists() or base_dir.is_absolute():
            return base_dir

        project_root_candidate = Path(__file__).resolve().parent.parent / base_dir
        if project_root_candidate.exists():
            return project_root_candidate

        return base_dir

    @staticmethod
    def normalize_text(value: Optional[str]) -> str:
        if not value:
            return ""
        value = value.replace("ё", "е").replace("Ё", "Е")
        value = value.replace("\xa0", " ")
        value = SPACE_RE.sub(" ", value)
        return value.strip().lower()

    @staticmethod
    def clean_text(value: Optional[str]) -> str:
        if not value:
            return ""
        return SPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()

    @staticmethod
    def truncate_text(value: Optional[str], limit: int = 200) -> str:
        text = ProcurementReferenceRegistry.clean_text(value)
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    @staticmethod
    def normalize_okpd2(value: str) -> str:
        value = value.strip()
        if not OKPD_RE.fullmatch(value):
            raise ValueError(f"Некорректный ОКПД2: {value!r}")
        return value

    @staticmethod
    def normalize_ktru(value: str) -> str:
        value = value.strip()
        if not KTRU_RE.fullmatch(value):
            raise ValueError(f"Некорректный код КТРУ: {value!r}")
        return value

    @staticmethod
    def build_okpd_candidates(code: str) -> list[str]:
        parts = code.split(".")
        res = []
        for i in range(len(parts), 1, -1):
            base = ".".join(parts[:i-1])+"."
            res.extend([base + parts[i-1][:j+1] for j in range(len(parts[i-1]))])
        # print(res)
        return res

    @staticmethod
    def _is_allowed_appendix(row: dict[str, Any]) -> bool:
        table_id = (row.get("table_id") or "").strip()
        return table_id in {"table_01", "table_02"}

    @classmethod
    def _build_row_okpd_candidates(cls, okpd2: str) -> list[str]:
        return cls.build_okpd_candidates(cls.normalize_okpd2(okpd2))

    @classmethod
    def _codes_overlap_by_parent(cls, left_okpd2: str, right_okpd2: str) -> bool:
        left_parts = cls.normalize_okpd2(left_okpd2).split(".")
        right_parts = cls.normalize_okpd2(right_okpd2).split(".")
        common_length = min(len(left_parts), len(right_parts))
        return left_parts[:common_length] == right_parts[:common_length]

    def _find_okpd2_prefix_matches_in_allowed_appendices(self, okpd2: str) -> list[dict[str, Any]]:
        code = self.normalize_okpd2(okpd2)
        prefix = f"{code}.%"

        rows: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str, int]] = set()

        if self.sqlite_path.exists():
            connection = sqlite3.connect(self.sqlite_path)
            connection.row_factory = sqlite3.Row
            try:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    SELECT
                        okpd2,
                        name,
                        table_id,
                        table_title,
                        appendix_title,
                        row_index,
                        position,
                        raw_code_text,
                        row_json
                    FROM okpd_index
                    WHERE okpd2 LIKE ?
                      AND table_id IN ('table_01', 'table_02')
                    ORDER BY table_id, row_index
                    """,
                    (prefix,),
                )
                for row in cursor.fetchall():
                    payload = dict(row)
                    payload["row"] = json.loads(payload.pop("row_json"))
                    row_key = (
                        str(payload.get("table_id") or ""),
                        str(payload.get("okpd2") or ""),
                        int(payload.get("row_index") or 0),
                    )
                    if row_key in seen_keys:
                        continue
                    seen_keys.add(row_key)
                    rows.append(payload)
            finally:
                connection.close()

        if self.index_json_path.exists():
            payload = json.loads(self.index_json_path.read_text(encoding="utf-8"))
            for row in payload:
                row_code = row.get("okpd2")
                if not isinstance(row_code, str):
                    continue
                if not row_code.startswith(f"{code}."):
                    continue
                if not self._is_allowed_appendix(row):
                    continue
                row_key = (
                    str(row.get("table_id") or ""),
                    str(row.get("okpd2") or ""),
                    int(row.get("row_index") or 0),
                )
                if row_key in seen_keys:
                    continue
                seen_keys.add(row_key)
                rows.append(row)

        return rows

    def _load_rows_from_sqlite(self, okpd2: str) -> list[dict[str, Any]]:
        if not self.sqlite_path.exists():
            return []

        connection = sqlite3.connect(self.sqlite_path)
        connection.row_factory = sqlite3.Row
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT
                    okpd2,
                    name,
                    table_id,
                    table_title,
                    appendix_title,
                    row_index,
                    position,
                    raw_code_text,
                    row_json
                FROM okpd_index
                WHERE okpd2 = ?
                ORDER BY table_id, row_index
                """,
                (okpd2,),
            )
            rows: list[dict[str, Any]] = []
            for row in cursor.fetchall():
                payload = dict(row)
                payload["row"] = json.loads(payload.pop("row_json"))
                rows.append(payload)
            return rows
        finally:
            connection.close()

    def _load_rows_from_json(self, okpd2: str) -> list[dict[str, Any]]:
        if not self.index_json_path.exists():
            return []

        payload = json.loads(self.index_json_path.read_text(encoding="utf-8"))
        return [row for row in payload if row.get("okpd2") == okpd2]

    def find_okpd2(self, okpd2: str) -> list[dict[str, Any]]:
        code = self.normalize_okpd2(okpd2)

        rows = self._load_rows_from_sqlite(code)
        if rows:
            return rows

        return self._load_rows_from_json(code)

    def find_okpd2_in_allowed_appendices(self, okpd2: str) -> list[dict[str, Any]]:
        return [row for row in self.find_okpd2(okpd2) if self._is_allowed_appendix(row)]

    def find_okpd2_with_fallback(self, okpd2: str) -> tuple[Optional[str], list[dict[str, Any]], list[str]]:
        code = self.normalize_okpd2(okpd2)
        checked_candidates = self.build_okpd_candidates(code)

        allowed_rows: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str, int]] = set()

        for candidate in checked_candidates:
            matched_rows: list[dict[str, Any]] = []

            for row in self.find_okpd2(candidate) + self._find_okpd2_prefix_matches_in_allowed_appendices(candidate):
                if not self._is_allowed_appendix(row):
                    continue
                row_key = (
                    str(row.get("table_id") or ""),
                    str(row.get("okpd2") or ""),
                    int(row.get("row_index") or 0),
                )
                if row_key in seen_keys:
                    continue
                seen_keys.add(row_key)
                allowed_rows.append(row)

            for row in allowed_rows:
                row_code = row.get("okpd2")
                if not row_code:
                    continue
                if self._codes_overlap_by_parent(candidate, row_code):
                    matched_rows.append(row)

            if matched_rows:
                return candidate, matched_rows, checked_candidates

        return None, [], checked_candidates

    def _pick_best_candidate(
        self,
        candidates: list[dict[str, Any]],
        query_name: Optional[str],
    ) -> tuple[dict[str, Any], bool, bool, float]:
        normalized_query_name = self.normalize_text(query_name)

        best: Optional[dict[str, Any]] = None
        best_score = float("-inf")
        best_exact_name = False
        best_normalized_name = False
        best_similarity = 0.0

        for candidate in candidates:
            reference_name = candidate.get("name") or candidate.get("reference_name") or ""
            normalized_reference_name = self.normalize_text(reference_name)

            exact_name = bool(query_name and reference_name == query_name)
            normalized_name = bool(
                normalized_query_name and normalized_query_name == normalized_reference_name
            )
            similarity = (
                SequenceMatcher(None, normalized_query_name, normalized_reference_name).ratio()
                if normalized_query_name and normalized_reference_name
                else 0.0
            )

            score = similarity
            if normalized_name:
                score += 10
            if exact_name:
                score += 20

            if score > best_score:
                best = candidate
                best_score = score
                best_exact_name = exact_name
                best_normalized_name = normalized_name
                best_similarity = similarity

        assert best is not None
        return best, best_exact_name, best_normalized_name, best_similarity

    def check_okpd2(self, okpd2: str, name: Optional[str] = None) -> MatchResult:
        query_code = self.normalize_okpd2(okpd2)
        matched_code, candidates, checked_candidates = self.find_okpd2_with_fallback(query_code)

        if not candidates or matched_code is None:
            checked_str = ", ".join(checked_candidates)
            return MatchResult(
                found=False,
                source="pp_1875",
                query_okpd2=query_code,
                matched_okpd2=None,
                is_parent_match=False,
                checked_candidates=checked_candidates,
                query_name=name,
                exact_okpd_match=False,
                exact_name_match=False,
                normalized_name_match=False,
                similarity=0.0,
                table_id=None,
                table_title=None,
                reference_name=None,
                position=None,
                row=None,
                message=(
                    f"Код {query_code} не найден в приложениях 1 и 2 локального справочника ПП РФ № 1875.\n "
                    f"Проверены префиксы: {checked_str}."
                ),
            )

        best, exact_name_match, normalized_name_match, similarity = self._pick_best_candidate(
            candidates=candidates,
            query_name=name,
        )

        reference_name = best.get("name") or best.get("reference_name")
        short_table_title = self.truncate_text(best.get("table_title"), limit=200)
        exact_okpd_match = matched_code == query_code
        is_parent_match = not exact_okpd_match

        if not name:
            if exact_okpd_match:
                message = (
                    f"<warn><ins>Обратите внимание</ins> Код {query_code} <ins>Входит в перечень</ins></warn> '{short_table_title}'.\n"
                    f"Эталонное наименование: {reference_name}.\n"
                    f"<ins>Необходимо учесть требования постановления при проведении закупки.</ins>"
                )
            else:
                message = (
                    f"<warn><ins>Обратите внимание</ins> на Код {query_code}.\n"
                    f'Родительский код {matched_code} <ins>Входит в перечень</ins></warn> "{short_table_title}".\n'
                    f"Эталонное наименование: {reference_name}.\n"
                    f"Проверьте соответствует ли ваше наименование: '{name}'."
                    f"<ins>Необходимо учесть требования постановления при проведении закупки.</ins>"
                )
        else:
            if exact_okpd_match and (exact_name_match or normalized_name_match):
                message = (
                    f"<warn><ins>Обратите внимание</ins> Код {query_code} <ins>Входит в перечень</ins></warn> '{short_table_title}'.\n"
                    f"<ins>Необходимо учесть требования постановления при проведении закупки.</ins>"
                )
            elif exact_okpd_match:
                message = (
                    f"<warn><ins>Обратите внимание</ins> Код {query_code} <ins>Входит в перечень</ins> </warn>\n"
                    f"'{short_table_title}',\n"
                    f"<ins>но наименование отличается от эталонного.</ins>\n"
                    f"Эталонное наименование: {reference_name}.\n"
                    f"Проверьте соответствует ли ваше наименование: '{name}'.\n"
                    f"<ins>Необходимо учесть требования постановления при проведении закупки.</ins>"
                     
                )
            else:
                message = (
                    f"<warn><ins>Обратите внимание</ins> на Код {query_code}.</warn>\n"
                    f'Родительский код {matched_code} <ins>Входит в перечень</ins> "{short_table_title}".\n'
                    f"Эталонное наименование: {reference_name}.\n"
                    f"Проверьте соответствует ли ваше наименование: '{name}'."
                    f"<ins>Необходимо учесть требования постановления при проведении закупки.</ins>"
                )

        return MatchResult(
            found=True,
            source="pp_1875",
            query_okpd2=query_code,
            matched_okpd2=matched_code,
            is_parent_match=is_parent_match,
            checked_candidates=checked_candidates,
            query_name=name,
            exact_okpd_match=exact_okpd_match,
            exact_name_match=exact_name_match,
            normalized_name_match=normalized_name_match,
            similarity=similarity,
            table_id=best.get("table_id"),
            table_title=best.get("table_title"),
            reference_name=reference_name,
            position=best.get("position"),
            row=best.get("row"),
            message=message,
        )

    def _build_ktru_url(self, page_name: str, ktru_code: str) -> str:
        code = self.normalize_ktru(ktru_code)
        return f"{self.KTRU_BASE_URL}/{page_name}.html?itemId={code}"

    def _fetch_html(self, url: str, timeout: int = 60) -> str:
        try:
            response = self._http.get(
                url,
                headers=self.DEFAULT_HEADERS,
                timeout=(min(timeout, 15), timeout),
                verify=self._tls_verify,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 404:
                raise KTRUNotFoundError("Карточка КТРУ не найдена") from exc
            raise

        response.encoding = response.encoding or response.apparent_encoding or "utf-8"
        return response.text

    def _extract_main_name(self, soup: BeautifulSoup) -> Optional[str]:
        node = soup.select_one(".cardMainInfo__section .cardMainInfo__content")
        if node:
            text = self.clean_text(node.get_text(" ", strip=True))
            if text:
                return text

        for section in soup.select(".blockInfo__section.section"):
            title_node = section.select_one(".section__title")
            info_node = section.select_one(".section__info")
            if not title_node or not info_node:
                continue
            title = self.clean_text(title_node.get_text(" ", strip=True)).lower()
            if "наименование товара" in title:
                text = self.clean_text(info_node.get_text(" ", strip=True))
                if text:
                    return text

        return None

    def _extract_section_pairs(self, soup: BeautifulSoup) -> dict[str, str]:
        result: dict[str, str] = {}

        for section in soup.select(".blockInfo__section.section"):
            title_node = section.select_one(".section__title")
            info_node = section.select_one(".section__info")
            if not title_node or not info_node:
                continue

            title = self.clean_text(title_node.get_text(" ", strip=True))
            info = self.clean_text(info_node.get_text(" ", strip=True))

            if title and info:
                result[title] = info

        return result

    def _extract_summary_characteristics(self, soup: BeautifulSoup) -> dict[str, str]:
        result: dict[str, str] = {}

        nodes = soup.select(".sectionMainInfo__body .cardMainInfo__title")
        texts: list[str] = []
        for node in nodes:
            text = self.clean_text(node.get_text(" ", strip=True))
            if text:
                texts.append(text)

        combined = " ".join(texts)
        parts = [part.strip(" .;") for part in combined.split(";") if part.strip(" .;")]

        for part in parts:
            if ":" not in part:
                continue

            key, value = part.split(":", 1)
            key = self.clean_text(key)
            value = self.clean_text(value)

            if not key or not value:
                continue

            if key.lower().startswith("единица измерения"):
                continue

            result[key] = value

        return result

    def _split_characteristic_values(self, raw_value: Optional[str]) -> list[str]:
        text = self.clean_text(raw_value)
        if not text:
            return []

        values = [part.strip() for part in re.split(r"\s*[;\n\r]+\s*", text) if part.strip()]
        if len(values) == 1 and text.count(",") >= 1:
            comma_parts = [part.strip() for part in text.split(",") if part.strip()]
            if len(comma_parts) > 1 and all(len(part) <= 120 for part in comma_parts):
                values = comma_parts

        unique_values: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = self.normalize_text(value)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique_values.append(value)

        return unique_values

    def _extract_cell_text(self, cell: Any) -> str:
        parts: list[str] = []
        for fragment in cell.stripped_strings:
            text = self.clean_text(fragment)
            if text:
                parts.append(text)
        return "; ".join(parts)

    def _extract_characteristic_name_cell_text(self, cell: Any) -> str:
        first_div = cell.find("div", recursive=False)
        if first_div is not None:
            text = self.clean_text(first_div.get_text(" ", strip=True))
            if text:
                return text
        return self._extract_cell_text(cell)

    def _append_characteristic(
        self,
        result: dict[str, list[str]],
        name: Optional[str],
        raw_value: Optional[str],
    ) -> None:
        characteristic_name = self.clean_text(name)
        if not characteristic_name:
            return

        name_normalized = self.normalize_text(characteristic_name)
        if any(
            marker in name_normalized
            for marker in (
                "наименование характеристики",
                "значение характеристики",
                "характеристики товара",
            )
        ):
            return

        values = self._split_characteristic_values(raw_value)
        if not values:
            return

        bucket = result.setdefault(characteristic_name, [])
        seen = {self.normalize_text(item) for item in bucket}
        for value in values:
            normalized_value = self.normalize_text(value)
            if not normalized_value or normalized_value in seen:
                continue
            seen.add(normalized_value)
            bucket.append(value)

    def _extract_characteristics_from_tables(self, soup: BeautifulSoup) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}

        for table in soup.select("table"):
            rows = table.select("tr")
            if not rows:
                continue

            header_cells = rows[0].find_all(["th", "td"])
            header_texts = [self.clean_text(cell.get_text(" ", strip=True)) for cell in header_cells]
            normalized_headers = [self.normalize_text(text) for text in header_texts]

            name_idx: Optional[int] = None
            value_idx: Optional[int] = None

            for idx, header in enumerate(normalized_headers):
                if name_idx is None and (
                    "наименование характеристики" in header
                    or header == "характеристика"
                    or (header.startswith("наименование") and "характерист" in header)
                ):
                    name_idx = idx
                if value_idx is None and (
                    "значение характеристики" in header
                    or header == "значение"
                    or header.endswith("значения")
                ):
                    value_idx = idx

            if name_idx is None and value_idx is None:
                for row in rows:
                    cells = row.find_all(["th", "td"])
                    if len(cells) < 2:
                        continue
                    self._append_characteristic(
                        result=result,
                        name=self._extract_cell_text(cells[0]),
                        raw_value=self._extract_cell_text(cells[-1]),
                    )
                continue

            data_rows = rows[1:]
            if name_idx is None:
                name_idx = 0
            if value_idx is None:
                value_idx = 1 if len(header_texts) > 1 else 0

            for row in data_rows:
                cells = row.find_all(["th", "td"])
                if len(cells) <= max(name_idx, value_idx):
                    continue

                self._append_characteristic(
                    result=result,
                    name=self._extract_cell_text(cells[name_idx]),
                    raw_value=self._extract_cell_text(cells[value_idx]),
                )

        return result

    def _extract_characteristics_from_ktru_description_table(
        self,
        soup: BeautifulSoup,
    ) -> dict[str, list[str]]:
        detailed = self._extract_detailed_characteristics_from_ktru_description_table(soup)
        result: dict[str, list[str]] = {}
        for name, payload in detailed.items():
            result[name] = list(payload.get("values") or [])
        return result

    def _extract_characteristic_required_flag(self, cell: Any) -> Optional[bool]:
        marker = cell.select_one(".revert")
        marker_text = self._extract_cell_text(marker) if marker is not None else self._extract_cell_text(cell)
        normalized = self.normalize_text(marker_text)
        if "не является обязательной" in normalized:
            return False
        if "является обязательной" in normalized:
            return True
        return None

    def _extract_detailed_characteristics_from_ktru_description_table(
        self,
        soup: BeautifulSoup,
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        table = soup.select_one("#ktruCharacteristicContent table.blockInfo__table")
        if table is None:
            return result

        current_name: Optional[str] = None
        current_required: Optional[bool] = None

        for row in table.select("tbody tr"):
            cells = row.find_all("td", recursive=False)
            if not cells:
                continue

            if len(cells) >= 3:
                current_name = self._extract_characteristic_name_cell_text(cells[0])
                current_required = self._extract_characteristic_required_flag(cells[0])
                value_cell = cells[1]
            elif len(cells) == 2:
                value_cell = cells[0]
            else:
                continue

            if not current_name:
                continue

            item = result.setdefault(
                current_name,
                {
                    "values": [],
                    "required": bool(current_required),
                },
            )
            if current_required is True:
                item["required"] = True

            raw_value = self._extract_cell_text(value_cell)
            values = item["values"]
            before = len(values)
            self._append_characteristic(
                result={current_name: values},
                name=current_name,
                raw_value=raw_value,
            )
            if len(values) == before and not values and raw_value:
                values.append(raw_value)

        return result

    def parse_ktru_characteristics_html(self, html: str) -> dict[str, list[str]]:
        soup = BeautifulSoup(html, "html.parser")
        parsed = self._extract_characteristics_from_ktru_description_table(soup)
        if parsed:
            return parsed
        return self._extract_characteristics_from_tables(soup)

    def parse_ktru_common_info_html(self, html: str, ktru_code: str) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")

        section_pairs = self._extract_section_pairs(soup)
        summary_characteristics = self._extract_summary_characteristics(soup)
        name = self._extract_main_name(soup)

        okpd2_raw = section_pairs.get("Код по ОКПД2")
        okpd2_code = None
        okpd2_name = None

        if okpd2_raw:
            if ":" in okpd2_raw:
                left, right = okpd2_raw.split(":", 1)
                okpd2_code = self.clean_text(left)
                okpd2_name = self.clean_text(right)
            else:
                okpd2_code = self.clean_text(okpd2_raw)

        unit = section_pairs.get("Единицы измерения (количество товара, объем работы, услуги по ОКЕИ)")
        application_date_start = section_pairs.get("Дата начала обязательного применения позиции каталога")
        application_date_end = section_pairs.get("Дата окончания применения позиции каталога")

        short_description: list[str] = []

        if unit:
            short_description.append(f"Единица измерения: {unit}")

        for key, value in summary_characteristics.items():
            short_description.append(f"{key}: {value}")

        return {
            "ktru_code": ktru_code,
            "name": name,
            "okpd2_code": okpd2_code,
            "okpd2_name": okpd2_name,
            "unit": unit,
            "application_date_start": application_date_start,
            "application_date_end": application_date_end,
            "summary_characteristics": summary_characteristics,
            "short_description": short_description,
            "section_pairs": section_pairs,
        }

    def get_ktru_common_info(self, ktru_code: str) -> dict[str, Any]:
        code = self.normalize_ktru(ktru_code)
        url = self._build_ktru_url("ktru-description", code)
        try:
            html = self._fetch_html(url)
        except KTRUNotFoundError:
            # Compatibility with installations where the former tab URL remains active.
            url = self._build_ktru_url("commonInfo", code)
            html = self._fetch_html(url)

        parsed = self.parse_ktru_common_info_html(html, code)
        self._ktru_html_cache[code] = html
        parsed["url"] = url
        parsed["html"] = html
        return parsed

    def get_ktru_characteristics_detailed(self, ktru_code: str) -> dict[str, dict[str, Any]]:
        code = self.normalize_ktru(ktru_code)
        html = self._ktru_html_cache.get(code)
        if html is None:
            url = self._build_ktru_url("ktru-description", code)
            html = self._fetch_html(url)
            self._ktru_html_cache[code] = html
        soup = BeautifulSoup(html, "html.parser")

        parsed = self._extract_detailed_characteristics_from_ktru_description_table(soup)
        if parsed:
            return parsed

        fallback = self._extract_characteristics_from_tables(soup)
        return {
            name: {
                "values": list(values),
                "required": False,
            }
            for name, values in fallback.items()
        }

    def get_ktru_characteristics(self, ktru_code: str) -> dict[str, list[str]]:
        parsed = self.get_ktru_characteristics_detailed(ktru_code)
        return {
            name: list(payload.get("values") or [])
            for name, payload in parsed.items()
        }

    def get_ktru_short_description(self, ktru_code: str) -> str:
        payload = self.get_ktru_common_info(ktru_code)

        name = payload.get("name")
        short_description = payload.get("short_description") or []

        lines: list[str] = []
        if name:
            lines.append(f"Наименование: {name}")

        if short_description:
            lines.append("")
            lines.append("Краткое описание:")
            lines.extend(f"- {item}" for item in short_description)

        return "\n".join(lines)

    @staticmethod
    def _parse_russian_date(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        cleaned = ProcurementReferenceRegistry.clean_text(value)
        try:
            return datetime.strptime(cleaned, "%d.%m.%Y").date()
        except ValueError:
            return None

    def _build_ktru_error_result(
        self,
        code: str,
        name: Optional[str],
        message: str,
    ) -> KTRUMatchResult:
        return KTRUMatchResult(
            found=False,
            source="ktru",
            query_ktru=code,
            query_name=name,
            exact_ktru_match=False,
            exact_name_match=False,
            normalized_name_match=False,
            similarity=0.0,
            ktru_code=None,
            okpd2_code=None,
            okpd2_name=None,
            reference_name=None,
            unit=None,
            short_description=[],
            common_info_url=self._build_ktru_url("ktru-description", code),
            payload=None,
            message=message,
        )

    def check_ktru(self, ktru_code: str, name: Optional[str] = None) -> KTRUMatchResult:
        code = self.normalize_ktru(ktru_code)

        try:
            common_info = self.get_ktru_common_info(code)
        except KTRUNotFoundError:
            return self._build_ktru_error_result(
                code=code,
                name=name,
                message=f"Не удалось найти карточку КТРУ {code}\n",
            )
        except requests.RequestException as exc:
            return self._build_ktru_error_result(
                code=code,
                name=name,
                message=(
                    f"Не удалось получить карточку КТРУ {code}: "
                    f"{type(exc).__name__}\n"
                ),
            )

        reference_name = common_info.get("name")
        short_description = common_info.get("short_description") or []
        section_pairs = common_info.get("section_pairs") or {}
        status = self.clean_text(section_pairs.get("Статус"))
        exclusion_date_raw = section_pairs.get("Дата исключения позиции КТРУ")
        exclusion_date = self._parse_russian_date(exclusion_date_raw)

        normalized_query_name = self.normalize_text(name)
        normalized_reference_name = self.normalize_text(reference_name)

        exact_name_match = bool(name and reference_name == name)
        normalized_name_match = bool(
            normalized_query_name and normalized_query_name == normalized_reference_name
        )
        similarity = (
            SequenceMatcher(None, normalized_query_name, normalized_reference_name).ratio()
            if normalized_query_name and normalized_reference_name
            else 0.0
        )

        if exclusion_date and exclusion_date <= date.today():
            exclusion_date_text = exclusion_date.strftime("%d.%m.%Y")
            status_suffix = f" Статус: {status}." if status else ""
            message = (
                f"<warn><ins>Обратите внимание</ins>, КТРУ {code} исключено из каталога. "
                f"Дата исключения: {exclusion_date_text}."
                f"{status_suffix}"
                f"</warn>"
            )
            found = False
        elif not reference_name:
            message = f"КТРУ {code} найден, но наименование автоматически извлечь не удалось.\n"
            found = False
        elif not name:
            lines = [f"Наименование: {reference_name}"]
            if short_description:
                lines.append("")
                lines.append("Краткое описание:")
                lines.extend(f"- {item}" for item in short_description)

            message = "\n".join(lines)
            found = True
        elif exact_name_match or normalized_name_match:
            message = (
                f"КТРУ {code} найден.\n\n"
                f"Ссылка на карточку: {common_info.get('url')}\n\n"
                f"Наименование совпадает с эталонной записью КТРУ.\n"
                f"Наименование: {reference_name}"
            )
            found = True
        else:
            message = (
                f"<warn>КТРУ {code} найден, но наименование отличается от эталонного.</warn>\n\n"
                f"Ссылка на карточку: {common_info.get('url')}\n\n"
                f"Эталонное наименование: {reference_name}\n"
                f"Проверьте соответствует ли ваше наименование <warn>'{name}'</warn>."
            )
            found = True

        return KTRUMatchResult(
            found=found,
            source="ktru",
            query_ktru=code,
            query_name=name,
            exact_ktru_match=True,
            exact_name_match=exact_name_match,
            normalized_name_match=normalized_name_match,
            similarity=similarity,
            ktru_code=common_info.get("ktru_code"),
            okpd2_code=common_info.get("okpd2_code"),
            okpd2_name=common_info.get("okpd2_name"),
            reference_name=reference_name,
            unit=common_info.get("unit"),
            short_description=short_description,
            common_info_url=common_info.get("url"),
            payload=common_info,
            message=message,
        )

    def register_ktru_provider(self, provider: Any) -> None:
        self._ktru_provider = provider
