# Запуск и точки входа

Описание составлено по текущему рабочему дереву, включая незакоммиченные изменения. Источники: `docker-compose.yml`, `Dockerfile`, `web/fileprocessor/views.py`, `celery-worker/tasks.py`, `summary_model/web_service.py`, CLI-модули и `shared_modules/llm_models.py`. Команды сверены чтением кода; при подготовке документации они не выполнялись. Тесты, сервисы, API и обработка документов не запускались.

## Точки входа

### Рабочий путь web/Celery

1. `web/manage.py` запускает Django; маршруты находятся в `web/textprocessor/urls.py` и `web/fileprocessor/urls.py`.
2. `web/fileprocessor/views.py` принимает файлы по ролям. Общая загрузка использует отдельный `summary_model.classification.upload_router.route_upload`; итоговая обработка идёт через те же поля ролей.
3. Web отправляет JSON с `key`, `label`, `name`, `content_b64` в задачу `rag_worker.process_document_query`.
4. `celery-worker/celery_app.py` регистрирует задачи из `tasks.py`. Worker декодирует файлы во временный каталог и вызывает `summary_model.web_service.process_uploaded_documents` с путями к ним.
5. Оркестратор использует `extraction_pipeline.extract_package`, отдельные VLM-пути, документное LLM-извлечение, семантические/этапные/штрафные проверки и сопоставление КП. Затем `checks.runner.run_checks` и `checks.report.build_checks_report_text` формируют результат.
6. Worker создаёт DOCX через `build_result_docx_bytes` в `celery-worker/tasks.py`, возвращает `ai_response`, `result_file_b64`, `result_file_name` и очищает временный каталог. Django показывает и выдаёт результат задачи.

Только `plan` обязателен. Для основных полей web валидирует DOCX; для обращения и пояснительной записки также PDF. КП поступают списком: сервис поддерживает DOCX, PDF, PNG, JPG/JPEG, WebP, но HTML-выбор файлов и CLI discovery не следует считать одинаковыми интерфейсами.

### Отдельные CLI и развиваемые компоненты

| Модуль | Вход и результат | Внешние вызовы |
| --- | --- | --- |
| `summary_model.full_pipeline_cli` | Каталог -> рабочий `web_service` -> текст отчёта, конечная схема, checks и диагностика. | По умолчанию включены LLM, VLM и КТРУ. |
| `summary_model.extraction_cli` | Каталог/manifest -> `ProcurementPackageExtraction`, схемы документов, таблицы и debug/payload JSON. Проверки и итоговый отчёт не запускаются. | LLM через `--with-llm`, VLM через `--with-vlm`; по умолчанию выключены. |
| `summary_model.checks_cli` | Сохранённый `ProcurementPackageExtraction` JSON -> `checks.json`, `report.txt`, `run.json`. | Только при `--with-llm` / `--with-ktru`. |
| `summary_model.cli` | DOCX/manifest -> `summary_model.service.process_package`: другая модель пакета, validation, analyzers и reporting. | Управляются `--no-llm`, `--no-external`, `--no-live-ktru`. |

Слой типизированного извлечения разрабатывается и отдельно, и внутри текущего web-пути. Независимый `service.py` экспортируется также через `summary_model/__init__.py`, но текущий worker его не вызывает. По коду нельзя определить, планируется ли его замена, развитие или дальнейшая интеграция; здесь он не объявляется устаревшим.

`parser_lab`, `vlm_lab`, `nmck_lab`, `commercial_offer_lab`, `structured_output_lab` служат отдельной диагностике и экспериментам. Они могут использовать рабочие парсеры/матчеры, однако сами по себе не воспроизводят весь web-путь. Названия labs не означают, что используемые ими компоненты экспериментальны во всех местах применения.

### Переход от RAG

Рассмотренный рабочий оркестратор получает данные из загруженных документов и конкретных нормативных справочников, а не из векторного поиска по корпусу. Типизированные поля, таблицы и evidence поступают в программные проверки; LLM/VLM читают сложное содержимое и помогают интерпретировать неоднозначные случаи. Embedding-функции в `shared_modules/llm_models.py` сохранены, но не участвуют в рассмотренных точках входа. Это подтверждает изменение архитектуры рабочего пути, а не завершённую миграцию всех компонентов.

## Зависимости и запуск

`Dockerfile` использует `python:3.12-slim` и устанавливает зависимости из:

- `web/requirements.txt`;
- `celery-worker/requirements.txt`;
- `shared_modules/requirements.txt`;
- `summary_model/requirements.txt`.

Затем копирует репозиторий в `/app` и задаёт `PYTHONPATH=/app`. Для локального CLI нужен корень репозитория как рабочий каталог и Python-окружение с соответствующими зависимостями. Пример установки в заранее выбранное окружение:

```bash
python -m pip install -r web/requirements.txt -r celery-worker/requirements.txt -r shared_modules/requirements.txt -r summary_model/requirements.txt
```

Сборка, установка и разрешение версий при обновлении документации не проверялись запуском. Redis нужен web/Celery, но не самостоятельным CLI.

### Docker Compose

Создайте `web/.env` по примеру в [README](../README.md#быстрый-локальный-запуск), затем из корня:

```bash
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 worker
```

Интерфейс: `http://localhost:8000/`. Остановка:

```bash
docker compose down
```

Для Compose v1 используется `docker-compose` вместо `docker compose`.

Фактические команды Compose:

- web: `python web/manage.py migrate`, затем `python web/manage.py runserver 0.0.0.0:8000`;
- worker: из `celery-worker/` запускается `celery -A celery_app worker --loglevel=info --pool=threads --concurrency=4`;
- Redis: образ `redis:7`, порт хоста 6379; web публикует 8000.

Compose читает `web/.env` для web и worker, но блок `environment` переопределяет `DEBUG=False`, `ALLOWED_HOSTS=*` для web и адреса Celery для обоих сервисов. Изменение `ALLOWED_HOSTS` только в `.env` не меняет поведение этого Compose. Django `runserver`, опубликованный Redis без указанного пароля и отсутствие описанного production-сервера означают, что этот конфиг нельзя представлять как завершённую production-конфигурацию. Persistent volumes в нём не заданы.

### Без Docker

Django загружает `web/.env` через `manage.py`/settings. Для web можно указать `ALLOWED_HOSTS=localhost,127.0.0.1` и `CELERY_BROKER_URL=redis://localhost:6379/0`, `CELERY_RESULT_BACKEND=redis://localhost:6379/0`.

Celery app сам не загружает `web/.env`: нужные переменные следует передать в окружение процесса до запуска. Загрузка ключа через фабрику LLM-клиента не заменяет раннюю настройку всего worker. Корень проекта должен быть доступен для импортов; пример PowerShell из корня:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python web/manage.py migrate
python web/manage.py runserver 127.0.0.1:8000
```

В отдельном терминале с настроенными переменными и доступным Redis:

```powershell
Set-Location celery-worker
celery -A celery_app worker --loglevel=info --pool=threads --concurrency=4
```

Это команды существующих точек входа, а не результаты проверки локального развёртывания.

## Переменные окружения

Никакие реальные ключи для документации не нужны. Не сохраняйте их в README, диагностике или коммитах.

| Переменная | Использование и фактический default |
| --- | --- |
| `SECRET_KEY` | Django; задайте своё значение. В settings есть шаблонный fallback. |
| `DEBUG`, `ALLOWED_HOSTS` | Django; settings default: `True` и пустой список. Compose переопределяет, см. выше. |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Redis для задач и результатов; локальный fallback `redis://localhost:6379/0`, Compose задаёт `redis://redis:6379/0`. |
| `OPENAI_API_KEY` | Ключ совместимого провайдера; требуется для включённых модельных вызовов. |
| `OPENAI_BASE_URL` | Endpoint API. В коде default `https://api.hydraai.ru/v1`; для своего провайдера задайте явный URL. |
| `OPENAI_MODEL` | Основная текстовая модель; default `gpt-5-mini`. |
| `OPENAI_NANO_MODEL` | Модель отдельных вспомогательных вызовов; default `gpt-5-mini`. |
| `OPENAI_VLM_MODEL` | Модель визуального извлечения; default `gpt-5.4-mini`. |
| `OPENAI_FAST_MODEL` | Быстрый вспомогательный matcher; default `gemini-3.1-flash-lite`. |
| `SUMMARY_WITH_VLM_TABLES` | Worker: `1` включает VLM сложных таблиц; default `0`. |
| `SUMMARY_WITH_VLM_COMMERCIAL_OFFERS` | Worker: VLM КП, default `1`. |
| `SUMMARY_WITH_VLM_SHORT_DOCUMENTS` | Worker: VLM коротких PDF обращения/записки, default `1`. |
| `SUMMARY_LLM_CONCURRENCY` | Worker/full CLI: default `6`; не равно четырём потокам Celery. |
| `SUMMARY_VLM_MAX_TABLES_PER_DOCUMENT` | Лимит обычного table fallback: default `4`; специальные роли могут обрабатываться отдельно. |
| `SUMMARY_VLM_MAX_COMMERCIAL_OFFER_PAGES` | Лимит страниц КП: default `8`. |
| `SUMMARY_VLM_MAX_SHORT_DOCUMENT_PAGES` | Лимит страниц обращения/записки: default `4`. |
| `KTRU_TIMEOUT_SECONDS` | Worker/full CLI: timeout запроса КТРУ, default `30` секунд. |
| `KTRU_TRUST_ENV_PROXY` | Учёт proxy-окружения requests для КТРУ; по умолчанию выключен, включается `1`/`true`/`yes`. |
| `KTRU_CA_BUNDLE` | Путь к PEM CA bundle для КТРУ; при наличии используется вместо булевого TLS-режима. |
| `KTRU_VERIFY_TLS` | В текущем реестровом коде default `0` (проверка TLS выключена). `1` включает проверку; указанный CA bundle имеет приоритет. Это расходится с рекомендацией безопасного default в старой документации. |

Модели и base URL читаются в `shared_modules/llm_models.py` при импорте. Надёжный способ конфигурации всех путей: передавать их в окружение **до** запуска Python/worker. Full CLI вызывает `load_dotenv('web/.env')` внутри `main`, уже после импортов; это не гарантирует обновление ранее созданных констант моделей/URL. Docker передаёт окружение до импорта. Не каждый совместимый API предоставляет все default-модели.

Worker явно включает LLM extraction, semantic LLM и КТРУ в `tasks.py`; переменных отключения этих трёх этапов там нет. При самостоятельном вызове `web_service` они управляются `WebPipelineOptions`, в full CLI есть соответствующие флаги.

## CLI и диагностические артефакты

Все примеры запускаются из корня репозитория. Каталоги `<PACK>` / `<RUN>` заменяются своими путями. CLI не требует запуска Django или Celery.

### Полный рабочий оркестратор

```bash
python -m summary_model.full_pipeline_cli --input-dir "doci_primery/<PACK>" --output-dir "runtime/full_pipeline_runs/<RUN>" --no-ktru
```

Это внешний LLM/VLM-прогон: `--no-ktru` отключает live КТРУ, но не остальные модели и не обязательно все локальные нормативные проверки. Документы могут отправляться провайдеру и тарифицироваться. Правила сетевого запуска и ревью результатов: [agent_handoff.md](agent_handoff.md#live-api-runs).

Full CLI включает table VLM по умолчанию. Чтобы приблизить его к default worker, передайте `--no-vlm-tables`; при `SUMMARY_WITH_VLM_TABLES=1` в worker этот флаг не нужен. `SUMMARY_WITH_VLM_*` не заменяют CLI-флаги отключения. Другие опции также следует согласовать с окружением worker.

Discovery читает только файлы непосредственно в указанной папке. `analysis_result*`, временные Word-файлы и некоторые документы замечаний исключаются; роли определяются по именам. Для большинства ролей выбирается один файл с предпочтением DOCX, для КП сохраняются все распознанные файлы. Проверяйте `inputs.json`, особенно если есть варианты одного документа. В этом пути PDF обращения/записки поддержаны отдельно.

Результаты: `inputs.json`, `run.json`, `warnings.json`, `metrics.json`, `extraction_result.final.json`, `checks.json`, `report.txt`, `report_with_warnings.txt`, а при VLM также диагностические материалы таблиц. При исключении пишутся `run.json`/`error.json`; часть файлов может отсутствовать. Word-файл full CLI не создаёт: его renderer находится в worker.

### Только извлечение

```bash
python -m summary_model.extraction_cli --input-dir "<DOCX_PACK_DIR>" --output-dir "runtime/extraction_runs/<RUN>"
```

Без `--with-llm` и `--with-vlm` модельные вызовы выключены. Ключевой результат: `extraction_result.json`, а также `documents/`, `tables/`, `debug/`, `llm_payloads/` и `run.json`. Формирование payload не означает вызов LLM. При `--with-llm` появляются модельные артефакты и `extraction_result.llm.json`.

У `extraction_cli` отдельный media-маршрут: все поддержанные PDF/изображения идут в обработчик КП. Он не воспроизводит short-PDF маршрут `web_service`, даже если в manifest указан тип обращения. Поэтому для детерминированного примера здесь указан каталог DOCX, а поддержку PDF web нельзя автоматически переносить на этот CLI.

### Проверки сохранённой схемы

```bash
python -m summary_model.checks_cli --input "runtime/full_pipeline_runs/<RUN>/extraction_result.final.json" --output-dir "runtime/checks_runs/<RUN>"
```

Повторное извлечение не выполняется. `--with-llm` включает специализированные модельные проверки, `--with-ktru` включает внешний адаптер. Без них результаты зависимых проверок могут быть неполными; это не повторение полноценного live-прогона.

Подробности независимого `summary_model.cli` и labs: [передача работы](agent_handoff.md) и [исторический план миграции](summary_model_migration.md). Инструкции в исторических планах относятся к описанному там варианту, а не автоматически к текущему worker.

## Локальные справочники

Рабочий код использует `data/parsed_tables/`:

- `pp1875.sqlite`: поиск по локальным перечням через `services/procurement_reference_registry.py`;
- `okpd_index.json`: JSON-индекс и резервный источник реестрового поиска;
- `okpd2_official.json`: официальные наименования ОКПД2, используемые `summary_model/checks/okpd2_reference.py`;
- `table_01_appendix_1.json`, `table_02_appendix_2.json`, `table_03_appendix_3.json`, `tables_manifest.json`: подготовленные таблицы и метаданные.

Файлы должны оставаться доступными приложению; Docker копирует их вместе с кодом. Подготовка данных вынесена в `utils/parse_1875.py`, `utils/update_1875.py`, `utils/build_official_okpd2_reference.py`. Это отдельное обслуживание справочников, а не автоматическое обновление при проверке пакета. По наличию файлов нельзя гарантировать актуальность нормативной редакции.

SQLite Django (`web/db.sqlite3`) обслуживает стандартные механизмы Django и отличается от нормативного справочника. Основные результаты фоновой задачи возвращаются через Redis.

## Что остаётся неопределённым

- По коду не установлен план дальнейшего использования независимого `summary_model.service` и завершённость всех пунктов исторических roadmap.
- Доступность моделей конкретного провайдера, API и КТРУ, совместимость установки зависимостей и работоспособность текущего развёртывания не проверялись запуском.
- Актуальность локальных нормативных данных и полнота юридического покрытия не устанавливаются чтением точек входа. Сохранённые примеры и отчёты не являются общей гарантией качества.

Для архитектурных подробностей см. [project_guide.md](project_guide.md); для анализа конкретного результата используйте [правила ревью](agent_handoff.md#reviewing-algorithm-results).
