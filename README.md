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

Используйте рабочий файл [`web/.env`](web/.env).

Минимально заполните:

```env
SECRET_KEY=your-django-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

GIGACHAT_AUTH_KEY=your-gigachat-auth-key
GIGACHAT_MODEL=GigaChat-2-Max
GIGACHAT_TIMEOUT=180

OPENAI_API_KEY=your-openai-api-key
OPENAI_BASE_URL=https://api.proxyapi.ru/openai/v1
OPENAI_MODEL=gpt-5.3-chat-latest

CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

## Быстрый запуск через Docker Compose

Это основной и самый простой способ поднять сервис.

### 1. Создайте файл окружения

Заполните [`web/.env`](web/.env) реальными значениями переменных окружения.

Docker Compose читает этот файл через `env_file`, поэтому переменные автоматически попадут и в web, и в worker.

### 2. Запустите сервисы

Из корня проекта выполните:

```bash
docker compose up --build
```

Будут подняты:
- Redis
- Django web-сервер
- Celery worker

### 3. Откройте приложение

После запуска веб-интерфейс будет доступен по адресу:

```text
http://localhost:8000/
```

## Локальный запуск без Docker

### 1. Установите Redis

Нужен локальный Redis на `6379` либо настройте свои значения через переменные:
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`

### 2. Установите зависимости

Из корня проекта:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r web/requirements.txt
pip install -r celery-worker/requirements.txt
pip install -r latest_model/requirements.txt
pip install -r shared_modules/requirements.txt
```

### 3. Создайте файл окружения

Убедитесь, что файл [`web/.env`](web/.env) существует и содержит реальные значения переменных.

### 4. Выполните миграции Django

```bash
python web/manage.py migrate
```

### 5. Запустите веб-сервер

```bash
python web/manage.py runserver 0.0.0.0:8000
```

### 6. В отдельном терминале запустите worker

```bash
cd celery-worker && celery -A celery_app worker --loglevel=info --pool=threads --concurrency=4
```

После этого откройте [`http://localhost:8000/`](web/fileprocessor/urls.py:7).
