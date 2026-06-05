from __future__ import annotations

from bs4 import BeautifulSoup

from services.procurement_reference_registry import ProcurementReferenceRegistry


def test_parse_ktru_common_info_html_minimal(
    registry: ProcurementReferenceRegistry,
) -> None:
    html = """
    <html>
      <body>
        <div class="cardMainInfo__section">
          <div class="cardMainInfo__content">Зерно ржи</div>
        </div>

        <div class="blockInfo__section section">
          <div class="section__title">Код по ОКПД2</div>
          <div class="section__info">01.11.32.000: Культуры зерновые прочие</div>
        </div>

        <div class="blockInfo__section section">
          <div class="section__title">Единицы измерения (количество товара, объем работы, услуги по ОКЕИ)</div>
          <div class="section__info">Тонна;^метрическая тонна (1000 кг)</div>
        </div>

        <div class="blockInfo__section section">
          <div class="section__title">Дата начала обязательного применения позиции каталога</div>
          <div class="section__info">01.01.2024</div>
        </div>

        <div class="sectionMainInfo__body">
          <div class="cardMainInfo__title">Цвет: коричневый; Влажность: не более 14%</div>
        </div>
      </body>
    </html>
    """

    payload = registry.parse_ktru_common_info_html(
        html=html,
        ktru_code="01.11.32.000-00000002",
    )

    assert payload["ktru_code"] == "01.11.32.000-00000002"
    assert payload["name"] == "Зерно ржи"
    assert payload["okpd2_code"] == "01.11.32.000"
    assert payload["okpd2_name"] == "Культуры зерновые прочие"
    assert payload["unit"] == "Тонна;^метрическая тонна (1000 кг)"
    assert payload["application_date_start"] == "01.01.2024"
    assert payload["summary_characteristics"] == {
        "Цвет": "коричневый",
        "Влажность": "не более 14%",
    }
    assert payload["short_description"] == [
        "Единица измерения: Тонна;^метрическая тонна (1000 кг)",
        "Цвет: коричневый",
        "Влажность: не более 14%",
    ]


def test_extract_main_name_fallback_from_section_title(
    registry: ProcurementReferenceRegistry,
) -> None:
    html = """
    <html>
      <body>
        <div class="blockInfo__section section">
          <div class="section__title">Наименование товара, работы, услуги</div>
          <div class="section__info">Зерно ржи</div>
        </div>
      </body>
    </html>
    """

    payload = registry.parse_ktru_common_info_html(
        html=html,
        ktru_code="01.11.32.000-00000002",
    )

    assert payload["name"] == "Зерно ржи"


def test_parse_ktru_characteristics_html_table(
    registry: ProcurementReferenceRegistry,
) -> None:
    html = """
    <html>
      <body>
        <table>
          <tr>
            <th>Наименование характеристики</th>
            <th>Значение характеристики</th>
          </tr>
          <tr>
            <td>Цвет</td>
            <td>Белый; Черный</td>
          </tr>
          <tr>
            <td>Комплектация</td>
            <td>
              Кабель<br/>
              Адаптер
            </td>
          </tr>
          <tr>
            <td>Цвет</td>
            <td>Черный</td>
          </tr>
        </table>
      </body>
    </html>
    """

    payload = registry.parse_ktru_characteristics_html(html)

    assert payload == {
        "Цвет": ["Белый", "Черный"],
        "Комплектация": ["Кабель", "Адаптер"],
    }


def test_parse_ktru_characteristics_html_generic_two_column_table(
    registry: ProcurementReferenceRegistry,
) -> None:
    html = """
    <html>
      <body>
        <table>
          <tr>
            <td>Ширина</td>
            <td>10 см</td>
          </tr>
          <tr>
            <td>Высота</td>
            <td>20 см</td>
          </tr>
        </table>
      </body>
    </html>
    """

    payload = registry.parse_ktru_characteristics_html(html)

    assert payload == {
        "Ширина": ["10 см"],
        "Высота": ["20 см"],
    }


def test_parse_ktru_characteristics_html_ktru_table_with_rowspan(
    registry: ProcurementReferenceRegistry,
) -> None:
    html = """
    <html>
      <body>
        <div id="ktruCharacteristicContent">
          <table class="blockInfo__table tableBlock grayBorderBottom mt-0">
            <tbody class="tableBlock__body">
              <tr class="tableBlock__row">
                <td class="tableBlock__col tableBlock__col_first" rowspan="3">
                  <div>Длина волны, нм</div>
                  <div class="revert">(характеристика не является обязательной для применения)</div>
                </td>
                <td class="tableBlock__col">850</td>
                <td class="tableBlock__col"></td>
              </tr>
              <tr class="tableBlock__row">
                <td class="tableBlock__col">1310</td>
                <td class="tableBlock__col"></td>
              </tr>
              <tr class="tableBlock__row">
                <td class="tableBlock__col">1550</td>
                <td class="tableBlock__col"></td>
              </tr>
              <tr class="tableBlock__row">
                <td class="tableBlock__col tableBlock__col_first" rowspan="2">
                  <div>Интерфейс</div>
                </td>
                <td class="tableBlock__col tableBlock__col_last">SFP</td>
                <td class="tableBlock__col"></td>
              </tr>
              <tr class="tableBlock__row">
                <td class="tableBlock__col tableBlock__col_last">XFP</td>
                <td class="tableBlock__col"></td>
              </tr>
            </tbody>
          </table>
        </div>
      </body>
    </html>
    """

    payload = registry.parse_ktru_characteristics_html(html)

    assert payload == {
        "Длина волны, нм": ["850", "1310", "1550"],
        "Интерфейс": ["SFP", "XFP"],
    }


def test_extract_detailed_ktru_characteristics_with_required_flags(
    registry: ProcurementReferenceRegistry,
) -> None:
    html = """
    <html>
      <body>
        <div id="ktruCharacteristicContent">
          <table class="blockInfo__table tableBlock grayBorderBottom mt-0">
            <tbody class="tableBlock__body">
              <tr class="tableBlock__row">
                <td class="tableBlock__col tableBlock__col_first" rowspan="2">
                  <div>Длина волны, нм</div>
                  <div class="revert">(характеристика не является обязательной для применения)</div>
                </td>
                <td class="tableBlock__col">850</td>
                <td class="tableBlock__col"></td>
              </tr>
              <tr class="tableBlock__row">
                <td class="tableBlock__col">1310</td>
                <td class="tableBlock__col"></td>
              </tr>
              <tr class="tableBlock__row">
                <td class="tableBlock__col tableBlock__col_first" rowspan="2">
                  <div>Интерфейс</div>
                  <div class="revert">(характеристика является обязательной для применения)</div>
                </td>
                <td class="tableBlock__col tableBlock__col_last">SFP</td>
                <td class="tableBlock__col"></td>
              </tr>
              <tr class="tableBlock__row">
                <td class="tableBlock__col tableBlock__col_last">XFP</td>
                <td class="tableBlock__col"></td>
              </tr>
            </tbody>
          </table>
        </div>
      </body>
    </html>
    """

    payload = registry._extract_detailed_characteristics_from_ktru_description_table(
        BeautifulSoup(html, "html.parser")
    )

    assert payload == {
        "Длина волны, нм": {
            "values": ["850", "1310"],
            "required": False,
        },
        "Интерфейс": {
            "values": ["SFP", "XFP"],
            "required": True,
        },
    }
