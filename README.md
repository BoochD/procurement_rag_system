# Procurement RAG Legal Checker

Сервис для проверки комплектов закупочных документов с помощью RAG-пайплайна и LLM.

Система принимает комплект документов закупки, извлекает из них данные, сопоставляет пункты между собой и возвращает результат проверки в веб-интерфейсе и в виде `.docx`-отчёта.

## Структура проекта

- [`web/`](web) — Django веб-приложение
- [`celery-worker/`](celery-worker) — Celery worker для асинхронной обработки
- [`latest_model/`](latest_model) — основная AI/RAG-логика
- [`shared_modules/`](shared_modules) — общие модули: парсинг, retriever, модели
- [`data/`](data) — локальные справочники и подготовленные таблицы
- [`services/`](services) — вспомогательные сервисы работы с реестром
- [`tests/`](tests) — автотесты для реестров и парсинга

## Что обязательно нужно для работы

Для запуска приложения нужны:
- Python 3.12
- Redis
- ключи доступа к LLM-провайдерам
- зависимости из файлов requirements

Основной сценарий работы такой:
1. пользователь загружает документы через [`web/fileprocessor/templates/fileprocessor/index.html`](web/fileprocessor/templates/fileprocessor/index.html)
2. Django отправляет задачу в Celery через [`web/fileprocessor/views.py`](web/fileprocessor/views.py:53)
3. worker обрабатывает документы через [`celery-worker/tasks.py`](celery-worker/tasks.py:103)
4. AI-логика запускается через [`latest_model/ai_service.py`](latest_model/ai_service.py:58)
5. результат возвращается в интерфейс и формируется `.docx`-файл

## Переменные окружения

Используйте файл [`web/.env`](web/.env).

Минимально заполните его так:

```env
SECRET_KEY=your-django-secret-key
DEBUG=False
ALLOWED_HOSTS=89.23.101.85,localhost,127.0.0.1

OPENAI_API_KEY=your-openai-api-key
OPENAI_BASE_URL=https://api.proxyapi.ru/openai/v1

CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
```

В проекте используется OpenAI-совместимый провайдер. `OPENAI_API_KEY` и `OPENAI_BASE_URL` задаются через окружение, а chat- и embedding-модели централизованно зафиксированы в `shared_modules/llm_models.py`.

## Независимый summary pipeline

Новый schema-first pipeline разрабатывается отдельно от текущего web/Celery сценария:

```bash
python -m summary_model.cli \
  --input-dir "doci_primery/PACK_06_05" \
  --output-dir "runtime/summary_runs/PACK_06_05"
```

Для локальной проверки без LLM и внешней сети:

```bash
python -m summary_model.cli \
  --input-dir "doci_primery/PACK_06_05" \
  --output-dir "runtime/summary_runs/PACK_06_05" \
  --no-llm \
  --no-external
```

CLI создаёт `Document IR`, summaries, canonical package, findings и текстовый/DOCX-отчёт. Текущий worker пока продолжает использовать `latest_model`.

## Быстрый запуск через Docker Compose

Это основной и самый простой способ поднять сервис на сервере.

### 1. Подключитесь к серверу

Подключитесь по SSH:

```bash
ssh root@89.23.101.85
```

### 2. Перейдите в папку проекта

Если репозиторий ещё не клонирован:

```bash
git clone https://github.com/BoochD/procurement_rag_system.git
cd procurement_rag_system
```

Если репозиторий уже есть на сервере:

```bash
cd procurement_rag_system
git pull
```

### 3. Создайте и заполните `web/.env`

Заполните [`web/.env`](web/.env) реальными значениями переменных окружения.

Важно:
- в `ALLOWED_HOSTS` укажите IP сервера или домен
- для сервера не оставляйте только `localhost`
- если будете заходить по IP `89.23.101.85`, он должен быть указан в `ALLOWED_HOSTS`

Пример:

```env
ALLOWED_HOSTS=89.23.101.85,localhost,127.0.0.1
```

### 4. Запустите сервисы

Из корня проекта выполните:

```bash
docker-compose up --build -d
```

Будут подняты:
- Redis
- Django web-сервер
- Celery worker

### 5. Откройте приложение

После запуска веб-интерфейс будет доступен по адресу:

```text
http://89.23.101.85:8000/
```

Если у сервера будет домен, используйте его вместо IP.

### 6. Как обновить проект на сервере

Если вы уже внесли изменения в репозиторий и хотите обновить контейнеры на сервере:

```bash
git pull
docker-compose down
docker-compose up --build -d
```
