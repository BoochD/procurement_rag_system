from summary_model.classification.upload_router import route_upload_text
from summary_model.domain.models import DocumentType


def test_routes_known_document_types_from_name_and_content():
    examples = [
        (
            "3. заявка_в_ПГ.docx",
            "Заявка на включение в план-график Код позиции КТРУ Способ определения поставщика "
            "Начальная (максимальная) цена контракта Этапы исполнения контракта",
            DocumentType.PLAN,
            "plan",
        ),
        (
            "0. Обращение о проведении закупки.docx",
            "Обращение о проведении закупки Просим провести закупку Приложение:",
            DocumentType.REQUEST,
            "obrasheniye",
        ),
        (
            "1. ОНМЦК.docx",
            "Обоснование начальной (максимальной) цены Метод сопоставимых рыночных цен "
            "Источники информации о ценах Минимальная цена за ед.",
            DocumentType.ONMCK,
            "onmck",
        ),
        (
            "2. Описание объекта закупки.docx",
            "ОПИСАНИЕ ОБЪЕКТА ЗАКУПКИ Функциональные, технические и качественные характеристики",
            DocumentType.OOZ,
            "ooz",
        ),
        (
            "5. Проект контракта.docx",
            "ПРОЕКТ КОНТРАКТА Предмет контракта Права и обязанности сторон "
            "Ответственность сторон Порядок приемки Реквизиты сторон",
            DocumentType.CONTRACT,
            "contract",
        ),
        (
            "4. Пояснительная записка.pdf",
            "ПОЯСНИТЕЛЬНАЯ ЗАПИСКА Обоснование необходимости закупки",
            DocumentType.EXPLANATORY_NOTE,
            "zapiska",
        ),
    ]

    for name, text, expected_type, expected_field in examples:
        result = route_upload_text(name, text)
        assert result.document_type == expected_type
        assert result.target_field == expected_field
        assert result.auto_assign is True


def test_contract_outranks_embedded_purchase_description():
    result = route_upload_text(
        "5. Контракт_4.docx",
        "ПРОЕКТ КОНТРАКТА Предмет контракта Права и обязанности сторон Ответственность сторон "
        "Порядок приемки Реквизиты сторон ПРИЛОЖЕНИЕ № 1 ОПИСАНИЕ ОБЪЕКТА ЗАКУПКИ "
        "Функциональные, технические и качественные характеристики",
    )

    assert result.document_type == DocumentType.CONTRACT
    assert result.target_field == "contract"


def test_request_is_not_reclassified_from_attachment_names():
    result = route_upload_text(
        "0. Обращение о проведении закупки.docx",
        "ОБРАЩЕНИЕ О ПРОВЕДЕНИИ ЗАКУПКИ Просим провести закупку. Приложение: "
        "заявка на включение в план-график; описание объекта закупки; проект контракта; ОНМЦК.",
    )

    assert result.document_type == DocumentType.REQUEST
    assert result.target_field == "obrasheniye"


def test_generated_report_is_never_routed():
    result = route_upload_text(
        "analysis_result (3).docx",
        "ОПИСАНИЕ ОБЪЕКТА ЗАКУПКИ Проект контракта Обращение о проведении закупки",
    )

    assert result.document_type == DocumentType.UNKNOWN
    assert result.target_field is None
    assert result.auto_assign is False


def test_commercial_offer_is_redirected_to_separate_control():
    result = route_upload_text(
        "КП_1.pdf",
        "КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ Направляем коммерческое предложение",
    )

    assert result.document_type == DocumentType.COMMERCIAL_OFFER
    assert result.target_field is None
    assert "отдельный блок" in result.reason


def test_ambiguous_document_remains_unresolved():
    result = route_upload_text("Документ.docx", "Материалы закупки")

    assert result.document_type == DocumentType.UNKNOWN
    assert result.auto_assign is False

