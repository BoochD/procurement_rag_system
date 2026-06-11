import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared_modules.parser_functions import DocumentParser
from shared_modules.retriever import BM25TextRetriever

from latest_model.check_registry import compare_characteristics, get_regestry_response_okpd_ktry
from latest_model.docs_parsing import (
    _parse_contract_points,
    _parse_ooz_points,
    _parse_onmck_pricies,
    _parse_onmck_text,
    _parse_plan_points,
    _parse_zapiska_text,
)
from latest_model.rag_processing import process_rag_points
from latest_model.smart_processing import process_smart_points

BASE_DIR = Path(__file__).resolve().parent.parent
REGISTRY_DIR = BASE_DIR / "data" / "parsed_tables"


def filter_plan_points(plan_points: List[str], keywords: List[str]) -> List[str]:
    plan_points_use = [
        plan_point
        for plan_point in plan_points
        if any(keyword.lower() in plan_point.lower() for keyword in keywords)
    ]
    return plan_points_use


def highlight_error_labels(text: str) -> str:
    """
    Помечает слово "Ошибки" служебным тегом для дальнейшей отрисовки красным
    и в HTML, и в Word-документе.
    """
    ok_placeholder = "__OK_NO_ERRORS_BLOCK__"
    ok_blocks = []

    def _store_ok_block(match: re.Match) -> str:
        ok_blocks.append(f"<ok>{match.group(1)}</ok>")
        return f"{ok_placeholder}{len(ok_blocks) - 1}__"

    text = re.sub(
        r"(?im)(<b>Ошибки:</b>\s*\n-\s*не обнаружены)",
        _store_ok_block,
        text,
    )
    text = re.sub(r"(?i)(Ошибки:?)", r"<error>\1</error>", text)

    for idx, ok_block in enumerate(ok_blocks):
        text = text.replace(f"{ok_placeholder}{idx}__", ok_block)

    return text


def _join_bullets(items: List[str], empty_text: str) -> str:
    if not items:
        return f"- {empty_text}"
    return "\n".join(f"- {item}" for item in items)


class AIService:
    def process_query(
        self,
        plan_path: Optional[str] = None,
        contract_path: Optional[str] = None,
        ooz_path: Optional[str] = None,
        zapiska_path: Optional[str] = None,
        ONMCK_path: Optional[str] = None,
        Obrasheniye_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        doc_labels = {
            "plan": "Заявка в план-график",
            "contract": "Проект контракта",
            "ooz": "ООЗ",
            "zapiska": "Пояснительная записка",
            "onmck": "ОНМЦК",
            "obrasheniye": "Обращение о проведении закупки",
        }
        doc_paths = {
            "plan": plan_path,
            "contract": contract_path,
            "ooz": ooz_path,
            "zapiska": zapiska_path,
            "onmck": ONMCK_path,
            "obrasheniye": Obrasheniye_path,
        }
        available_docs = [label for key, label in doc_labels.items() if doc_paths[key]]
        missing_docs = [label for key, label in doc_labels.items() if not doc_paths[key]]
        smart_available_documents = "\n".join(f"- {label}" for label in available_docs) or "- нет"
        performed_checks: List[str] = []
        skipped_checks: List[str] = []

        # -----------------------------------------------------------------------
        #                               ПЛАН-ГРАФИК
        # -----------------------------------------------------------------------
        if plan_path:
            try:
                plan_points = _parse_plan_points(plan_path)
                performed_checks.append("Извлечение данных из плана-графика")
            except Exception as e:
                plan_points = [f"Ошибка при парсинге плана-графика: {str(e)}"]
        else:
            plan_points = ["Документ 'Заявка в план-график' не загружен"]
            skipped_checks.append("Извлечение данных из плана-графика")

        smart_keywords = [
            "Код ОКПД",
            "Код позиции КТРУ",
            "Количество",
        ]
        rag_keywords = [
            "Сроки поставки",
            "цена контракта",
        ]

        plan_points_use = filter_plan_points(plan_points, smart_keywords)
        plan_points_str = "\n".join(plan_points_use).strip()

        if not plan_points_str:
            plan_points_str = "В плане-графике отсутствуют ОКПД, КТРУ или количество"

        plan_points_rag = filter_plan_points(plan_points, rag_keywords)
        procurement_method = filter_plan_points(
            plan_points,
            ["Способ выбора поставщика", "Способ выбора поставщика/исполнителя"],
        )
        plan_points_names = filter_plan_points(
            plan_points,
            ["Наименование объекта"],
        )

        try:
            okpd_plan = filter_plan_points(
                plan_points,
                ["ОКПД"],
            )[0].split(":")[1].split("-")[0].strip()
        except Exception:
            okpd_plan = None

        # -----------------------------------------------------------------------
        #            ПУНКТЫ КОНТРАКТА, ООЗ, ЗАПИСКИ, ОНМЦК
        # -----------------------------------------------------------------------
        if contract_path:
            try:
                contract_points = _parse_contract_points(contract_path)
                performed_checks.append("Извлечение данных из проекта контракта")
            except Exception as e:
                contract_points = f"Ошибка при парсинге контракта: {str(e)}"
        else:
            contract_points = "Документ 'Проект контракта' не загружен"
            skipped_checks.append("Извлечение данных из проекта контракта")

        if ooz_path:
            try:
                ooz_points = _parse_ooz_points(ooz_path)
                performed_checks.append("Извлечение данных из ООЗ")
            except Exception as e:
                ooz_points = f"Ошибка при парсинге ООЗ: {str(e)}"
        else:
            ooz_points = "Документ 'ООЗ' не загружен"
            skipped_checks.append("Извлечение данных из ООЗ")

        if zapiska_path:
            try:
                zapiska_points = _parse_zapiska_text(zapiska_path)
                performed_checks.append("Извлечение данных из пояснительной записки")
            except Exception as e:
                zapiska_points = f"Ошибка при парсинге пояснительной записки: {str(e)}"
        else:
            zapiska_points = "Документ 'Пояснительная записка' не загружен"
            skipped_checks.append("Извлечение данных из пояснительной записки")

        if ONMCK_path:
            try:
                ONMCK_points = _parse_onmck_text(ONMCK_path)
                performed_checks.append("Извлечение данных из ОНМЦК")
            except Exception as e:
                ONMCK_points = f"Ошибка при парсинге ОНМЦК: {str(e)}"
        else:
            ONMCK_points = "Документ 'ОНМЦК' не загружен"
            skipped_checks.append("Извлечение данных из ОНМЦК")

        # -----------------------------------------------------------------------
        #                 ПРОВЕРКА КТРУ И ОКПД НА САЙТЕ
        # -----------------------------------------------------------------------
        if plan_path and plan_points_use:
            try:
                res_ktry, res_okpd = get_regestry_response_okpd_ktry(
                    plan_points_use,
                    REGISTRY_DIR,
                    plan_points_names=plan_points_names,
                )
                performed_checks.append("Проверка КТРУ через сервис zakupki.gov.ru")
                performed_checks.append("Проверка ОКПД на вхождение в постановление 1875")
            except Exception as e:
                res_ktry, res_okpd = [f"Ошибка проверки КТРУ: {e}"], [f"Ошибка проверки ОКПД: {e}"]
        else:
            res_ktry = ["Проверка не выполнена: отсутствуют данные плана-графика для проверки КТРУ"]
            res_okpd = ["Проверка не выполнена: отсутствуют данные плана-графика для проверки ОКПД"]
            skipped_checks.append("Проверка КТРУ через сервис zakupki.gov.ru")
            skipped_checks.append("Проверка ОКПД на вхождение в постановление 1875")

        ktry_check_result = "\n-----------------------------------------------------------------------\n".join(res_ktry)
        okpd_check_result = "\n-----------------------------------------------------------------------\n".join(res_okpd)

        # -----------------------------------------------------------------------
        #                     КТРУ + ОКПД + количество
        # -----------------------------------------------------------------------
        try:
            smart_answer = process_smart_points(
                plan_points=plan_points_str,
                contract_points=contract_points,
                OOZ_points=ooz_points,
                zapiska_points=zapiska_points,
                ONMCK_points=ONMCK_points,
                available_documents=smart_available_documents,
            )
            performed_checks.append("Внутренний анализ по КТРУ, ОКПД и количеству")
        except Exception as e:
            smart_answer = f"Не удалось сформулировать ответ по КТРУ и ОКПД. Ошибка {e}"

        # -----------------------------------------------------------------------
        #               ПРОВЕРКА ХАРАКТЕРИСТИК НА САЙТЕ
        # -----------------------------------------------------------------------
        if ooz_path:
            try:
                characteristics_compare_result = compare_characteristics(
                    ooz_path,
                    procurement_method,
                    okpd_plan,
                    REGISTRY_DIR,
                )
                performed_checks.append("Сравнение характеристик из ООЗ с КТРУ на сайте")
                if isinstance(characteristics_compare_result, dict):
                    if "error" in characteristics_compare_result:
                        characteristics_compare_result = (
                            "<error>" + str(characteristics_compare_result["error"]) + "</error>"
                        )
                    else:
                        rendered_blocks = []
                        for code, payload in characteristics_compare_result.items():
                            if isinstance(payload, str):
                                if payload.strip().lower() == "всё ок":
                                    rendered_blocks.append(
                                        f"{code}: <ok>Характеристики удовлетворяют критериям с сайта</ok>"
                                    )
                                else:
                                    rendered_blocks.append(f"{code}: <error>{payload}</error>")
                            elif isinstance(payload, dict):
                                if "field_errors" in payload:
                                    block_lines = [f"{code}:"]
                                    reason = payload.get("reason")
                                    selected_okpd2 = payload.get("selected_okpd2")
                                    procurement_method_label = payload.get("procurement_method")
                                    can_add = payload.get("can_add_extra_characteristics")
                                    field_errors = payload.get("field_errors") or {}

                                    if procurement_method_label:
                                        block_lines.append(f"- Способ закупки: {procurement_method_label}")
                                    if selected_okpd2:
                                        block_lines.append(f"- Выбранный ОКПД2: {selected_okpd2}")
                                    if reason:
                                        block_lines.append(f"- Основание: {reason}")

                                    if can_add is True:
                                        block_lines.append(
                                            "- Дополнительные характеристики: <ok>разрешены</ok>"
                                        )
                                    elif can_add is False:
                                        block_lines.append(
                                            "- Дополнительные характеристики: <error>запрещены</error>"
                                        )
                                    else:
                                        block_lines.append(
                                            "- Дополнительные характеристики: <warn>не удалось определить однозначно, применена базовая строгая проверка</warn>"
                                        )

                                    if field_errors:
                                        for field_name, message in field_errors.items():
                                            block_lines.append(f"- {field_name}: <error>{message}</error>")
                                    else:
                                        block_lines.append("- <ok>Ошибки не обнаружены</ok>")
                                else:
                                    block_lines = [f"{code}:"]
                                    for field_name, message in payload.items():
                                        block_lines.append(f"- {field_name}: <error>{message}</error>")
                                rendered_blocks.append("\n".join(block_lines))
                            else:
                                rendered_blocks.append(str(payload))

                        characteristics_compare_result = "\n\n".join(rendered_blocks)
                else:
                    characteristics_compare_result = str(characteristics_compare_result)
            except Exception:
                characteristics_compare_result = (
                    "<error>Не удалось сравнить характеристики ООЗ с КТРУ на сайте.</error>"
                )
        else:
            characteristics_compare_result = "Проверка не выполнена: документ 'ООЗ' не загружен"
            skipped_checks.append("Сравнение характеристик из ООЗ с КТРУ на сайте")

        # -----------------------------------------------------------------------
        #                                 RAG часть
        # -----------------------------------------------------------------------
        if Obrasheniye_path:
            try:
                parser_Obrasheniye = DocumentParser(Obrasheniye_path)
                Obrasheniye_full_text = parser_Obrasheniye.extract_clean_text().strip()
                if not Obrasheniye_full_text:
                    Obrasheniye_full_text = "Не удалось извлечь данные из обращения о проведении закупки"
                else:
                    performed_checks.append("Извлечение текста из обращения о проведении закупки")
            except Exception:
                Obrasheniye_full_text = "Не удалось извлечь данные из обращения о проведении закупки"
        else:
            Obrasheniye_full_text = "Документ 'Обращение о проведении закупки' не загружен"
            skipped_checks.append("Извлечение текста из обращения о проведении закупки")

        if contract_path:
            try:
                parser_contract = DocumentParser(contract_path)
                contract_full_text = parser_contract.extract_clean_text().strip()
                if not contract_full_text:
                    contract_full_text = "Не удалось извлечь данные из текста контракта"
            except Exception:
                contract_full_text = "Не удалось извлечь данные из текста контракта"
        else:
            contract_full_text = "Документ 'Проект контракта' не загружен"

        if ooz_path:
            try:
                parser_ooz = DocumentParser(ooz_path)
                ooz_plain_text = parser_ooz.extract_clean_text().strip()
                if not ooz_plain_text:
                    ooz_plain_text = "Не удалось извлечь данные из документа ООЗ"
            except Exception:
                ooz_plain_text = "Не удалось извлечь данные из документа ООЗ"
        else:
            ooz_plain_text = "Документ 'ООЗ' не загружен"

        if ONMCK_path:
            try:
                parser_onmck = DocumentParser(ONMCK_path)
                onmck_plain_text = parser_onmck.extract_clean_text().strip()
                if not onmck_plain_text:
                    onmck_plain_text = "Не удалось извлечь данные из ОНМЦК"
            except Exception:
                onmck_plain_text = "Не удалось извлечь данные из ОНМЦК"
        else:
            onmck_plain_text = "Документ 'ОНМЦК' не загружен"

        try:
            rag_answer = ""
            if plan_points_rag:
                bm25 = BM25TextRetriever()
                retriever = bm25.create_retriever(
                    texts=[
                        contract_full_text,
                        zapiska_points,
                        ooz_plain_text,
                        onmck_plain_text,
                        Obrasheniye_full_text,
                    ],
                    n=7,
                    sources=[
                        "Проект контракта",
                        "Пояснительная записка",
                        "ООЗ",
                        "ОНМЦК",
                        "Обращение о проведении закупки",
                    ],
                )
                rag_answer = process_rag_points(retriever, plan_points_rag)
                performed_checks.append("RAG-анализ по найденным фрагментам документов")
            else:
                rag_answer = "RAG-анализ не выполнен: в плане-графике не найдены пункты для RAG-проверки"
                skipped_checks.append("RAG-анализ по найденным фрагментам документов")
        except Exception as e:
            rag_answer = f"Не удалось сформулировать RAG-ответ. Ошибка: {e}"

        # -----------------------------------------------------------------------
        #                                ЦЕНЫ ОНМЦК
        # -----------------------------------------------------------------------
        if ONMCK_path:
            try:
                price_check = _parse_onmck_pricies(ONMCK_path)
                performed_checks.append("Сравнение цен услуг поставщиков в ОНМЦК")
            except Exception:
                price_check = "Не удалось сравнить цены поставщиков в ОНМЦК"
        else:
            price_check = "Проверка не выполнена: документ 'ОНМЦК' не загружен"
            skipped_checks.append("Сравнение цен услуг поставщиков в ОНМЦК")

        docs_summary = (
            "<b>0) Комплектность пакета и выполненные проверки:</b>\n"
            + "<b>Загруженные документы:</b>\n"
            + _join_bullets(available_docs, "нет")
            + "\n\n<b>Отсутствующие документы:</b>\n"
            + _join_bullets(missing_docs, "нет")
            + "\n\n<b>Выполненные проверки:</b>\n"
            + _join_bullets(performed_checks, "нет")
            + "\n\n<b>Пропущенные проверки:</b>\n"
            + _join_bullets(skipped_checks, "нет")
        )

        # -----------------------------------------------------------------------
        #                 Ответ: проверка КТРУ и ОКПД + SMART + RAG
        # -----------------------------------------------------------------------
        final_parts = [part for part in [smart_answer, rag_answer] if part]
        final_response = "\n\n".join(final_parts)

        final_response = (
            docs_summary
            + "\n\n<b>1) Проверка КТРУ через сервис zakupki.gov.ru:</b>\n\n"
            + ktry_check_result
            + "\n\n"
            + "\n<b>2) Проверка ОКПД на вхождение в постановление 1875:</b>\n\n"
            + okpd_check_result
            + "\n\n"
            + "\n<b>3) Внутренний анализ перечня документов:</b>\n"
            + final_response
            + "\n\n<b>4) Сравнение характеристик из ООЗ с КТРУ на сайте:</b>\n\n"
            + characteristics_compare_result
            + "\n\n<b>5) Сравнение цен услуг поставщиков в ОНМЦК:</b>\n"
            + price_check
        )

        final_response = highlight_error_labels(final_response)
        return {"ai_response": final_response}


_ai_service_instance: Optional[AIService] = None


def get_ai_service() -> AIService:
    global _ai_service_instance
    if _ai_service_instance is None:
        _ai_service_instance = AIService()
    return _ai_service_instance
