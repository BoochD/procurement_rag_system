# План перехода на `summary_model`

## Текущий статус реализации

Реализован независимый первый вариант:

- Pydantic domain schemas и единый `Finding`;
- ordered DOCX reader и `Document IR`;
- merged-cell coordinates, table headers и deterministic table kinds;
- classifier с `type_hint` и content-based fallback;
- общая structured-output LLM infrastructure и отдельные prompts для всех типов документов;
- секционное/chunked извлечение контракта;
- deterministic fallback для запуска без LLM;
- canonical package и базовые правила комплектности, кодов, цен, количества, единиц, сроков и места;
- adapters для ОКПД2, КТРУ и характеристик;
- text/DOCX report builder;
- CLI и JSON artifacts;
- focused tests в `tests/summary_model_tests/`.

Pipeline пока не подключён к Celery/web. PDF/OCR, полное покрытие юридических правил и приёмка live LLM extraction остаются следующими этапами.

### Асинхронный analysis pipeline (schema v4)

- Повторяемые сроки, места, цены, НДС и гарантии хранятся списками
  `ExtractedValue`; одинаковые значения из разных мест сохраняются с отдельным
  evidence.
- Все документы и chunks извлекаются асинхронно через общий semaphore с
  лимитом трёх LLM-запросов.
- Табличные позиции ООЗ и контракта извлекаются deterministic parser-ом:
  LLM получает только текстовые условия этих документов. После LLM запрещено
  принимать коды без строгой повторной классификации regex ОКПД2/КТРУ.
- Публичный async API: `await summary_model.aprocess_package(...)`.
  Синхронный `process_package(...)` остаётся обёрткой для CLI и worker.
- После extraction параллельно запускаются `items_consistency`,
  `delivery_and_finance` и `legal_and_completeness`.
- Deterministic engine отвечает за комплектность, точные коды, числа и
  арифметику. Проверки PP №1875, КТРУ и характеристик остаются строгими
  внешними проверками и имеют приоритет над LLM.
- Ошибка одного analyzer не прерывает пакет и формирует `uncertain` finding.
- `run.json.metrics` содержит длительность этапов, количество LLM-вызовов,
  retry, причины retry, размер входа в символах и настройки reasoning/output
  без prompts, документов и секретов.
- Перед пакетными semantic analyzers nested evidence товарной позиции
  схлопываются в один item-level evidence. Полные evidence остаются в
  `DocumentSummary` и используются deterministic/external checks; изменение
  относится только к LLM wire payload.
- Агрегатные plan-строки с несколькими кодами и общим количеством не
  участвуют в per-item quantity/unit matching.
- В таблицах характеристик единица товара выбирается отдельно от единицы
  характеристики. Повторяющиеся КТРУ в разных строках считаются вариантами
  товара и не объединяются только по коду.
- Semantic LLM analyzers не оценивают допустимость характеристик товара.
  Эту проверку выполняет `ProcurementRegistryAdapter` для каждой позиции ООЗ:
  карточка КТРУ кешируется по коду, но повторяющиеся варианты одного КТРУ
  проверяются отдельно. Поддерживаются точные, числовые и диапазонные значения.
- Дополнительные характеристики разрешаются для закупки у единственного
  поставщика и запрещаются для ч. 12 ст. 93; при неопределённом способе
  формируется manual review.
- `--no-live-ktru` отключает как проверку существования КТРУ, так и получение
  допустимых/обязательных характеристик с сайта.
- Агрегатная key-value таблица плана разбирается deterministic: предмет,
  перечни ОКПД2/КТРУ и общее количество сохраняются даже при невалидном
  structured output LLM.
- HTTP-клиент КТРУ по умолчанию не наследует `HTTP_PROXY`/`HTTPS_PROXY` из
  терминала. Для осознанного использования системного proxy задаётся
  `KTRU_TRUST_ENV_PROXY=1`.
- Основная карточка КТРУ загружается через актуальный маршрут
  `ktru-description.html`; старый `commonInfo.html` используется только как
  fallback. HTML одной карточки кешируется для проверки характеристик.
- Сертификат Минцифры для Python можно передать через `KTRU_CA_BUNDLE`.
  Отключение проверки TLS доступно только явно через `KTRU_VERIFY_TLS=0` и не
  является production-настройкой.
- Итоговый отчёт формирует из `ProcurementPackage` обзорные блоки ОКПД2,
  КТРУ, товаров и количеств, НМЦК, сроков и мест поставки с группировкой
  по исходным документам. Технические успешные findings не дублируются;
  expected/actual/evidence показываются для ошибок и manual review.
- Порядок отчёта: комплектность, live-проверка КТРУ, ОКПД2/ПП №1875,
  внутренний анализ документов, внешняя проверка характеристик, цены
  поставщиков, остальные замечания.
- Технический `item_id` действует только внутри одного документа и не
  используется LLM как междокументный ключ. Дополнительные замечания
  группируются по финансам, гарантиям, СМП/СОНКО, национальному режиму,
  правам и штрафам.
- В значениях характеристик визуально одинаковые латинские и кириллические
  символы (`T/Т`, `H/Н`, `B/В` и другие) приводятся к общей форме.
- Сроки и места поставки извлекаются LLM как списки всех упоминаний с
  отдельным evidence. Если основной structured output оставил хотя бы одно
  из этих полей пустым, запускается один focused
  `DeliveryTermsExtraction` для документа; заполненные документы
  дополнительных вызовов не создают.
- Недоступность live-сервиса КТРУ является неопределённостью внешней
  проверки, а не ошибкой закупочной документации.
- `Document IR` хранит таблицы как `columns + physical rows + spans`.
  Отдельных объектов-ячеек и merged-копий нет; плотная matrix создаётся
  только временно внутри deterministic parser.
- Identity DOCX-ячейки определяется самим XML-элементом `_tc`, а не
  `id(cell._tc)`: числовой `id` временного wrapper может быть переиспользован
  Python и ошибочно объединить независимые ячейки.
- LLM получает таблицы как читаемые `TABLE/HEADER/SCOPE/ROW` записи.
  Многоуровневые headers задаются один раз, merged context выносится в
  `SCOPE`, а строки содержат только меняющиеся значения. Matrix и canonical
  row JSON в prompt не передаются. ООЗ и контрактные товарные таблицы LLM
  не передаются.
- Пустые DOCX paragraphs не сохраняются как `DocumentBlockIR`: source-order
  непустых блоков остаётся исходным и может содержать пропуски.

## Цель

Реализовать новый pipeline отдельно от `latest_model`, не переписывая работающий сервис на месте. Новый pipeline должен преобразовывать каждый документ в типизированную структуру, объединять документы в единый пакет закупки и выполнять проверяемые правила над структурированными данными.

Целевая схема:

```text
DOCX/PDF
  -> Document IR
  -> определение типа документа
  -> schema extraction для каждого документа
  -> canonical procurement package
  -> программные проверки
  -> внешние проверки
  -> LLM semantic checks
  -> findings
  -> HTML/DOCX report
```

`summary_model` не должен быть вторым монолитным `AIService`. Каждый слой должен иметь отдельный вход, выход и возможность локальной проверки.

## Что переиспользовать

Из текущего pipeline следует сохранить как отдельные сервисы:

- проверку ОКПД2 по локальным данным ПП РФ №1875 из `services/procurement_reference_registry.py`;
- получение КТРУ и характеристик с `zakupki.gov.ru`;
- правила сравнения характеристик из `latest_model/check_registry.py`;
- расчёт коэффициента вариации и сравнение цен поставщиков;
- базовые DOCX-утилиты, которые корректно читают абзацы и ячейки;
- Celery/web-контракт на первом этапе миграции;
- существующий формат findings-тегов только как временный адаптер для старого отчёта.

Не следует переносить без переоценки:

- эвристики на фиксированных окнах символов;
- сбор всего pipeline в одном методе;
- передачу неструктурированных фрагментов в один большой prompt;
- использование LLM для арифметики и точного сравнения кодов;
- привязку логики к конкретным названиям колонок одного шаблона.

## Предлагаемая структура

```text
summary_model/
  domain/
    common.py
    documents.py
    package.py
    findings.py
  ingestion/
    docx_reader.py
    pdf_reader.py - пока пусто
    document_ir.py
    table_normalizer.py
  classification/
    document_classifier.py
  extraction/
    base.py
    plan.py
    request.py
    commercial_offer.py
    onmck.py
    ooz.py
    contract.py
    explanatory_note.py
  validation/
    package_rules.py
    item_rules.py
    price_rules.py
    delivery_rules.py
    contract_rules.py
    semantic_rules.py
  external/
    procurement_registry.py
    organization_registry.py
  reporting/
    report_model.py
    legacy_adapter.py
  service.py
```

## Document IR

### Требования

`Document IR` является lossless-представлением документа перед LLM:

- сохраняет исходный порядок абзацев и таблиц;
- сохраняет текст без потери исходного значения;
- хранит координаты источника;
- описывает merged cells;
- связывает таблицу с ближайшим заголовком и окружающим текстом;
- не пытается заранее определить бизнес-смысл документа.

Минимальная схема:

```yaml
DocumentIR:
  document_id: string
  file_name: string
  media_type: docx | pdf | image
  blocks:
    - block_id: string
      order: integer
      type: paragraph | table | image | page_break
      text: string | null
      table: TableIR | null
      page: integer | null
  warnings: [string]

TableIR:
  table_id: string
  title: string | null
  context_before: [string]
  context_after: [string]
  row_count: integer
  columns:
    - index: integer
      alias: string
      header_path: [string]
  rows:
    - row_id: string
      row: integer
      values: object
      spans: object
  header_rows: [integer]
  kind: key_value | item_list | characteristics | supplier_matrix | specification | unknown
```

Каждое извлечённое значение должно ссылаться на источник:

```yaml
Evidence:
  document_id: string
  block_id: string
  table_id: string | null
  row: integer | null
  column: integer | null
  quote: string

ExtractedValue:
  raw_value: any
  normalized_value: any
  confidence: number
  evidence: [Evidence]
  warnings: [string]
```

## Универсальный парсинг таблиц

### 1. Извлечение

Для DOCX нужно обходить XML body, а не читать сначала все paragraphs, затем все tables. Это сохраняет реальный порядок блоков.

Для каждой таблицы сохраняются:

- origin values по физическим строкам и aliases колонок;
- `row_span`/`column_span` только для merged origins;
- header paths отдельно от значений строк;
- ближайшие непустые абзацы до и после таблицы.

Плотная matrix не сериализуется. `TableIR.matrix()` восстанавливает её в
памяти для deterministic parser и evidence quote restoration.

### 2. Нормализация заголовков

Программно:

1. Найти первые строки, похожие на заголовок.
2. Остановиться на первой строке данных.
3. Построить header path для каждой логической колонки по merged origins.
4. Удалить последовательные повторы внутри path.
5. Сохранить уровни header отдельно; не склеивать их в каждой строке.

Пример path:

```text
Цена товара / Поставщик 1 / Цена за ед.
Цена товара / Поставщик 1 / Стоимость
```

Aliases `c0`, `c1`, ... остаются стабильными для evidence и deterministic
parsers. Полный header path объясняется модели один раз через `HEADER`.

### 3. Определение типа таблицы

Сначала использовать программные признаки:

- 2-4 колонки `поле → значение` — `key_value`;
- `наименование`, `количество`, `единица` — `item_list`;
- `характеристика`, `значение характеристики` — `characteristics`;
- несколько поставщиков и пары `цена/стоимость` — `supplier_matrix`;
- цена, НДС, сумма — `specification`.

Если тип не определён уверенно, LLM получает headers, первые строки и контекст и возвращает только `table_kind` и mapping логических колонок.

### 4. Представление для LLM

LLM не получает сериализованный `TableIR`, массив объектов-ячеек или результат
`matrix()`. Перед вызовом строится отдельная читаемая текстовая проекция:

```text
TABLE table-1 "Таблица №1 Характеристики товара"
KIND: characteristics

HEADER L1: c0=№ п/п; c1=ОКПД2 / КТРУ; c2=Наименование; c3..c5=Требования
HEADER L2: c3=Наименование характеристики; c4=Значение; c5=Единица характеристики

SCOPE g1 rows=r2..r10:
c0=1; c1=20.59.12.120-00000002; c2=Картридж; c6=шт; c7=560

ROW r2 scope=g1: c3=Цвет тонера; c4=Черный
ROW r3 scope=g1: c3=Ресурс, страниц; c4=>= 8000
```

Header paths описываются один раз. Значения merged cells, общие для нескольких
физических строк, выносятся в `SCOPE`; строки содержат только изменяющиеся
значения. Для key-value таблиц используется форма `ROW r4: поле = значение`.

`TableIR.matrix()` существует только для deterministic parser и восстановления
evidence по координатам. Плотная матрица никогда не сериализуется в prompt.

Большие таблицы делятся по логическим строкам или позициям товара. Заголовки и контекст повторяются в каждом chunk. Строки одной позиции с continuation rows нельзя разделять между chunk-ами.

### 5. Границы ответственности

LLM может:

- определить смысл неизвестной колонки;
- сгруппировать continuation rows в одну позицию;
- сопоставить нестандартное название поля с полем schema;
- извлечь смысл сложного текстового условия.

Код должен:

- читать DOCX/XML;
- сохранять строки и колонки;
- нормализовать числа, деньги, даты, проценты и коды;
- считать суммы, минимумы, НДС и коэффициенты;
- проверять точное равенство;
- обнаруживать пропущенные обязательные поля.

## Общие доменные схемы

```yaml
Money:
  amount: decimal | null
  currency: string | null
  vat_rate: decimal | null
  vat_included: boolean | null

Period:
  raw_text: string
  value: integer | null
  unit: calendar_day | working_day | month | date_range | unknown
  anchor_event: string | null

ProcurementItem:
  item_id: string
  name: ExtractedValue
  okpd2: [ExtractedValue]
  ktru: [ExtractedValue]
  quantity: ExtractedValue | null
  unit: ExtractedValue | null
  unit_price: ExtractedValue | null
  total_price: ExtractedValue | null
  characteristics:
    - name: ExtractedValue
      value: ExtractedValue
      unit: ExtractedValue | null
      is_additional: boolean | null

ExecutionStage:
  name: string | null
  start: ExtractedValue | null
  end: ExtractedValue | null
  period: ExtractedValue | null
  amount: ExtractedValue | null

SecurityTerms:
  application_security: ExtractedValue | null
  contract_security: ExtractedValue | null
  warranty_security: ExtractedValue | null

SmpTerms:
  preference_enabled: boolean | null
  subcontracting_required: boolean | null
  subcontracting_percent: decimal | null
  sonko_applies: boolean | null

NationalRegimeTerms:
  prohibitions: [ExtractedValue]
  restrictions: [ExtractedValue]
  advantages: [ExtractedValue]
  pp1875_fields_completed: boolean | null
```

## Схемы документов

### Общая оболочка

```yaml
DocumentSummary:
  document_id: string
  detected_type: string
  classification_confidence: number
  classification_evidence: [Evidence]
  extraction_warnings: [string]
  unresolved_fields: [string]
```

### Заявка в план-график

```yaml
PlanRequestSummary:
  subject: ExtractedValue
  items: [ProcurementItem]
  nmck: ExtractedValue
  procurement_method: ExtractedValue
  single_supplier_basis: ExtractedValue | null
  delivery_places: [ExtractedValue]
  delivery_periods: [ExtractedValue]
  contract_execution_period: ExtractedValue | null
  execution_stages: [ExecutionStage]
  funding_source: ExtractedValue | null
  kbk: [ExtractedValue]
  security: SecurityTerms
  national_regime: NationalRegimeTerms
  smp_terms: SmpTerms
  additional_participant_requirements: ExtractedValue | null
  all_required_rows_completed: boolean | null
```

### Обращение о проведении закупки

```yaml
ProcurementRequestSummary:
  subject: ExtractedValue
  nmck: ExtractedValue
  procurement_method: ExtractedValue
  single_supplier_basis: ExtractedValue | null
  delivery_places: [ExtractedValue]
  delivery_periods: [ExtractedValue]
  execution_stages: [ExecutionStage]
  attachments:
    - name: ExtractedValue
      number: string | null
  funding_source: ExtractedValue | null
```

### Коммерческое предложение

```yaml
CommercialOfferSummary:
  supplier_name: ExtractedValue
  inn: ExtractedValue | null
  kpp: ExtractedValue | null
  requisites_present: boolean
  outgoing_number: ExtractedValue | null
  offer_date: ExtractedValue | null
  subject: ExtractedValue
  items: [ProcurementItem]
  subtotal: ExtractedValue | null
  vat: ExtractedValue | null
  total: ExtractedValue
  delivery_place: ExtractedValue | null
  delivery_period: ExtractedValue | null
  advance_payment: ExtractedValue | null
  validity_period: ExtractedValue | null
  ocr_used: boolean
  ocr_warnings: [string]
```

Пакет должен поддерживать список `commercial_offers`, а правило комплектности — минимум три предложения.

### Обоснование НМЦК

```yaml
OnmckSummary:
  pricing_method: ExtractedValue
  subject: ExtractedValue
  source_offers:
    - supplier_name: ExtractedValue | null
      outgoing_number: ExtractedValue | null
      offer_date: ExtractedValue | null
  items:
    - item: ProcurementItem
      supplier_prices:
        - supplier_ref: string
          unit_price: ExtractedValue
          total_price: ExtractedValue | null
      selected_unit_price: ExtractedValue
      calculated_total: ExtractedValue
      minimum_unit_price: ExtractedValue | null
  nmck: ExtractedValue
  variation_coefficients:
    - item_id: string
      coefficient: decimal
  calculation_notes: [ExtractedValue]
```

### Описание объекта закупки

```yaml
OozSummary:
  subject: ExtractedValue
  delivery_places: [ExtractedValue]
  delivery_periods: [ExtractedValue]
  execution_stages: [ExecutionStage]
  items: [ProcurementItem]
  warranty_terms: [ExtractedValue]
  extra_characteristics_justifications:
    - item_id: string | null
      justification: ExtractedValue
  trademarks:
    - value: ExtractedValue
      justification: ExtractedValue | null
  rights_transfer_required: boolean | null
  rights_transfer_documents: [ExtractedValue]
```

### Проект контракта

```yaml
ContractSummary:
  contract_number: ExtractedValue | null
  subject: ExtractedValue
  price: ExtractedValue
  vat_terms: ExtractedValue | null
  funding_source: ExtractedValue | null
  items: [ProcurementItem]
  delivery_places: [ExtractedValue]
  delivery_periods: [ExtractedValue]
  execution_period: ExtractedValue | null
  execution_stages: [ExecutionStage]
  warranty_terms: [ExtractedValue]
  security: SecurityTerms
  smp_terms: SmpTerms
  penalties:
    - violation_type: ExtractedValue
      amount_or_formula: ExtractedValue
  applications:
    - number: string | null
      name: ExtractedValue
      present_in_document: boolean
  typical_contract:
    applicable: boolean | null
    used: boolean | null
    reference: ExtractedValue | null
    required_terms_present: boolean | null
  smp_typical_terms_present: boolean | null
  treasury_or_bank_support: ExtractedValue | null
  rights_transfer_terms: [ExtractedValue]
```

### Пояснительная записка

```yaml
ExplanatoryNoteSummary:
  subject: ExtractedValue
  procurement_goal: ExtractedValue | null
  procurement_method: ExtractedValue | null
  single_supplier_basis: ExtractedValue | null
  nmck: ExtractedValue | null
  delivery_place: ExtractedValue | null
  delivery_period: ExtractedValue | null
  justification: ExtractedValue | null
```

## Canonical procurement package

```yaml
ProcurementPackage:
  plan: PlanRequestSummary | null
  request: ProcurementRequestSummary | null
  commercial_offers: [CommercialOfferSummary]
  onmck: OnmckSummary | null
  ooz: OozSummary | null
  contract: ContractSummary | null
  explanatory_note: ExplanatoryNoteSummary | null
  unknown_documents: [DocumentSummary]
  package_warnings: [string]
```

Автоматическая классификация не должна молча назначать тип при низкой уверенности. Такой документ помещается в `unknown_documents`, а пользователю возвращается запрос на подтверждение типа.

## Какие проверки выполнять программно

### Комплектность

- наличие обязательных типов документов;
- минимум три коммерческих предложения;
- соответствие списка приложений фактическим документам;
- заполненность обязательных полей schema.

### Точные междокументные сравнения

- ОКПД2 и КТРУ;
- НМЦК и цена контракта;
- количества и единицы измерения;
- цены за единицу и итоговые суммы;
- исходящие номера КП;
- места поставки после нормализации адресов;
- сроки после нормализации периодов;
- этапы исполнения;
- гарантийные сроки;
- размеры обеспечений;
- проценты СМП/СОНКО;
- источник финансирования и КБК;
- нумерация приложений.

### Арифметика

- `quantity × unit_price = total`;
- сумма позиций;
- НДС;
- минимум цен по каждой позиции;
- соответствие выбранной цены минимальному КП;
- коэффициент вариации;
- порог закупки 20 млн рублей;
- штрафы, если формула может быть выражена детерминированным правилом.

### Внешние проверки

- существование и статус КТРУ;
- ОКПД2 и национальный режим;
- допустимые/обязательные характеристики КТРУ;
- возможность дополнительных характеристик;
- статус организации по ИНН;
- применимость типового контракта и нормативных требований, если есть надёжный источник данных.

## Какие проверки выполнять через LLM

- классификация типа документа при неоднозначном названии;
- смысловое соответствие предмета закупки между документами;
- сопоставление похожих позиций при разных формулировках;
- выделение основания закупки у единственного поставщика;
- извлечение сложных сроков и условий с последующей программной нормализацией;
- определение назначения нестандартной таблицы или колонки;
- наличие смыслового обоснования дополнительных характеристик;
- наличие основания товарного знака;
- анализ условий передачи прав;
- поиск требуемых юридических условий в контракте;
- формулировка понятного объяснения уже найденного расхождения.

LLM не должен самостоятельно выставлять итог `passed/failed`, если правило можно вычислить программно. Он возвращает извлечённые факты, evidence и semantic observations; решение принимает rule engine.

## Findings

Все проверки должны возвращать единый формат:

```yaml
Finding:
  rule_id: string
  severity: info | warning | error | manual_review
  status: passed | failed | skipped | uncertain
  title: string
  message: string
  documents: [string]
  expected: any
  actual: any
  evidence: [Evidence]
  source: deterministic | external | llm
```

`uncertain` и `manual_review` обязательны. Система не должна заменять отсутствие данных выдуманным выводом.

## Этапы миграции

### Этап 0. Зафиксировать baseline

- сохранить несколько репрезентативных пакетов из `doci_primery/`;
- описать ожидаемые извлечённые значения и текущие результаты;
- зафиксировать формат старого Celery/web ответа.

### Этап 1. Document IR

- реализовать ordered DOCX reader;
- реализовать lossless `TableIR`;
- добавить JSON snapshots для разных типов таблиц;
- не использовать LLM на этом этапе.

Критерий готовности: все таблицы `PACK_06_05` представлены без потери строк, колонок и merged headers.

### Этап 2. Классификация и schema extraction

Порядок внедрения:

1. пояснительная записка;
2. обращение;
3. план-график;
4. ООЗ;
5. ОНМЦК;
6. коммерческие предложения и OCR;
7. проект контракта.

Начинать с детерминированных кандидатов, затем использовать LLM structured output для заполнения schema.

### Этап 3. Canonical package и rule engine

- реализовать комплектность;
- сравнение предмета, кодов, количества, единиц, цен и сроков;
- арифметику ОНМЦК;
- security/SMP/stage rules;
- единый `Finding`.

### Этап 4. Переиспользование внешних проверок

- обернуть текущий registry стабильными интерфейсами;
- передавать ему коды и характеристики из canonical schema;
- преобразовывать ответы registry в `Finding`;
- не смешивать HTML-теги с доменными результатами.

### Этап 5. Semantic checks

- добавить только проверки, которые нельзя надёжно выразить кодом;
- требовать evidence для каждого вывода;
- кэшировать extraction по hash документа;
- версионировать prompt и schema.

### Этап 6. Интеграция

- добавить `summary_model.service.process_package`;
- подключить его к worker за feature flag;
- сохранить legacy adapter для текущего HTML/DOCX;
- сравнивать старый и новый pipeline на одинаковых пакетах;
- переключить default только после ручной приёмки.

### Этап 7. Удаление старого pipeline

Удалять старые smart/RAG ветки только после того, как:

- новый pipeline покрывает обязательные документы;
- registry и цены перенесены;
- отчёт содержит не меньше полезной информации;
- есть rollback через feature flag;
- расхождения на baseline-пакетах разобраны.

## Основные риски

- LLM может менять структуру ответа: нужны schema validation и retry с ошибками валидации.
- Слишком ранняя нормализация может потерять юридически важную формулировку: всегда сохранять `raw_value` и evidence.
- Названия товаров могут быть похожими: автоматическое сопоставление должно иметь confidence и `manual_review`.
- Коммерческие предложения могут быть PDF/сканами: OCR является отдельным ненадёжным слоем.
- Большой контракт нельзя отправлять одним prompt: извлечение должно идти по разделам и таблицам.
- Новая архитектура не должна одновременно менять web/Celery/report contracts.

## Definition of Done

Новый pipeline считается готовым к переключению, когда:

- классифицирует обязательные типы документов и неизвестные документы;
- строит валидные schema с evidence;
- корректно разбирает все типы таблиц из baseline-пакетов;
- программно проверяет точные значения и арифметику;
- использует LLM только для semantic extraction/checks;
- переиспользует КТРУ/ОКПД2 и price checks;
- возвращает единый список findings;
- формирует текущий HTML/DOCX через adapter;
- поддерживает feature flag и rollback.
## Independent typed extraction layer

`summary_model.extraction_pipeline.extract_package(...)` is a separate parser-first
entrypoint for building `ProcurementPackageExtraction`. It is intentionally not wired
to web/Celery and does not run PP 1875, live KTRU, legal checks, package-level LLM
semantic analyzers, or OCR. Its purpose is to stabilize extraction JSON before
cross-document validators are rebuilt.

CLI:

```powershell
python -m summary_model.extraction_cli `
  --input-dir "doci_primery\PACK_06_05" `
  --output-dir "runtime\extraction_runs\PACK_06_05"
```

Artifacts:

- `extraction_result.json`
- `documents/*.json`
- `tables/*.json`
- `debug/tables/<file>/table_N_physical.md`
- `debug/tables/<file>/table_N_logical.json`
- `debug/tables/<file>/table_N_compact.md`
- `run.json`

`summary_model/tables/` adds `ParsedTable`, `HeaderPath`, `LogicalTableRow`,
`compact_markdown`, `compact_json`, and debug export on top of `TableIR v4`.
`TableIR` remains the compact physical representation; dense `matrix()` is used only
inside Python parsers.
