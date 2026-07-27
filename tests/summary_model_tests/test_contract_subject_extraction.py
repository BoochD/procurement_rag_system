from pathlib import Path

import pytest

from summary_model.checks.runner import _check_subject_against_plan
from summary_model.domain.models import DocumentType
from summary_model.extraction_pipeline import (
    _contract_draft,
    _purchase_description,
    _purchase_subject_from_ooz_section,
)
from summary_model.extraction_models import (
    ContractDraftSchema,
    ProcurementPackageExtraction,
    PurchaseDescriptionSchema,
    ScheduleApplicationSchema,
)
from summary_model.ingestion import read_docx
from summary_model.tables import extract_tables


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "ОПИСАНИЕ ОБЪЕКТА ЗАКУПКИ\nПоставка шин пневматических\nМесто поставки: Новосибирск",
            "Поставка шин пневматических",
        ),
        (
            "ОПИСАНИЕ ОБЪЕКТА ЗАКУПКИ\n1. Общие сведения\n"
            "1.1. Наименование закупки: оказание услуг по расширению ЦОД.\nОКПД2: 62.09.10.000",
            "оказание услуг по расширению ЦОД",
        ),
        (
            "ОПИСАНИЕ ОБЪЕКТА ЗАКУПКИ\n1. Наименование объекта закупки: Поставка мебели.\n"
            "Срок поставки: 30 дней",
            "Поставка мебели",
        ),
        (
            "ОПИСАНИЕ ОБЪЕКТА ЗАКУПКИ\n1. Наименование: Поставка трансиверов.\n"
            "Адрес поставки: Новосибирск",
            "Поставка трансиверов",
        ),
        (
            "Приложение № 1 ОПИСАНИЕ ОБЪЕКТА ЗАКУПКИ\n"
            "Наименование закупки: Поставка картриджей.\nМесто поставки: Новосибирск",
            "Поставка картриджей",
        ),
    ],
)
def test_purchase_subject_parser_handles_known_ooz_variants(text, expected):
    assert _purchase_subject_from_ooz_section(text) == expected


def test_purchase_subject_parser_ignores_attachment_list_reference():
    text = """
    Приложения:
    приложение № 1 «Описание объекта закупки»;
    приложение № 2 «Спецификация».
    Адреса и реквизиты Сторон
    ОПИСАНИЕ ОБЪЕКТА ЗАКУПКИ
    поставка шин пневматических и комплектующих для автомобилей
    Место поставки: г. Новосибирск
    """

    assert _purchase_subject_from_ooz_section(text) == (
        "поставка шин пневматических и комплектующих для автомобилей"
    )


CONTRACT_FIXTURES = [
    (
        "doci_primery/Cartridges/4. Проект контракта_картриджи.docx",
        "поставка расходных материалов для оргтехники",
    ),
    (
        "doci_primery/MEBEL_PACK/6. Проект контракта_поставка мебели в2 (1).docx",
        "Поставка офисной мебели",
    ),
    (
        "doci_primery/MONOBLOCK_PACK/4_Контракт_на_поставку_моноблоков_1.docx",
        "поставка моноблоков",
    ),
    (
        "doci_primery/PACK_06_05/4_Проект_контракта_шины_и_комплектующие.docx",
        "поставка шин пневматических и комплектующих для автомобилей",
    ),
    (
        "doci_primery/SHINY_PNEVMA_PACK/4. Проект контракта шины и комплектующие.docx",
        "поставка шин пневматических и комплектующих для автомобилей",
    ),
    (
        "doci_primery/TRANSIVER_PACK/4.Проект Контракта.docx",
        "Поставка трансиверов",
    ),
    (
        "doci_primery/Данные для тестирования 01.06.26/4. Проект контракта.docx",
        "оказание услуг по предоставлению (передаче) права использования программного обеспечения "
        "(программ для ЭВМ) на условиях простой (неисключительной) лицензии",
    ),
    (
        "doci_primery/закупка_для_примера_расширение_ЦОД_с_лицензиями/5. Контракт_4.docx",
        "оказание услуг по расширению вычислительных мощностей, среды виртуализации, системы "
        "резервного копирования центра обработки данных Правительства Новосибирской области",
    ),
]

OOZ_FIXTURES = [
    (
        "doci_primery/Cartridges/3. ООЗ Картридж_неориг_.docx",
        "поставка расходных материалов для оргтехники",
    ),
    (
        "doci_primery/MEBEL_PACK/4. ООЗ_поставка_мебели (1).docx",
        "Поставка офисной мебели",
    ),
    (
        "doci_primery/MONOBLOCK_PACK/3.ООЗ на поставку моноблоков (1).docx",
        "поставка ноутбуков",
    ),
    (
        "doci_primery/PACK_06_05/3. ООЗ автошины и комплектующие.docx",
        "поставка шин пневматических и комплектующих для автомобилей",
    ),
    (
        "doci_primery/SHINY_PNEVMA_PACK/3. ООЗ автошины и комплектующие.docx",
        "поставка шин пневматических и комплектующих для автомобилей",
    ),
    (
        "doci_primery/TRANSIVER_PACK/2.ООЗ_трансиверы v.3+.docx",
        "Поставка трансиверов",
    ),
    (
        "doci_primery/Данные для тестирования 01.06.26/3. ООЗ_Лицензии_на_ТД.docx",
        "оказание услуг по предоставлению (передаче) права использования программного обеспечения "
        "(программ для ЭВМ) на условиях простой (неисключительной) лицензии",
    ),
    (
        "doci_primery/закупка_для_примера_расширение_ЦОД_с_лицензиями/"
        "2. Описание объекта закупки_2.docx",
        "оказание услуг по расширению вычислительных мощностей, среды виртуализации, системы "
        "резервного копирования центра обработки данных Правительства Новосибирской области",
    ),
]


@pytest.mark.parametrize(("relative_path", "expected"), CONTRACT_FIXTURES)
def test_all_contract_fixtures_extract_exact_embedded_ooz_subject(relative_path, expected):
    repository_root = Path(__file__).resolve().parents[2]
    path = repository_root / relative_path
    ir = read_docx(path)
    schema = _contract_draft(ir, extract_tables(ir, DocumentType.CONTRACT))

    assert schema.embedded_purchase_description is not None
    assert schema.embedded_purchase_description.purchase_subject == expected


@pytest.mark.parametrize(("relative_path", "expected"), OOZ_FIXTURES)
def test_all_ooz_fixtures_extract_exact_subject(relative_path, expected):
    repository_root = Path(__file__).resolve().parents[2]
    path = repository_root / relative_path
    ir = read_docx(path)
    schema = _purchase_description(ir, extract_tables(ir, DocumentType.OOZ))

    assert schema.purchase_subject == expected


def test_subject_check_prefers_embedded_ooz_name_over_legal_contract_reference():
    package = ProcurementPackageExtraction(
        schedule_application=ScheduleApplicationSchema(purchase_subject="Поставка мебели"),
        contract_draft=ContractDraftSchema(
            subject="Поставка товара согласно описанию объекта закупки",
            embedded_purchase_description=PurchaseDescriptionSchema(
                purchase_subject="Поставка мебели"
            ),
        ),
    )

    result = _check_subject_against_plan(package)

    assert result.status == "passed"
    assert "Проект контракта: Поставка мебели" in result.details["summary_lines"]
