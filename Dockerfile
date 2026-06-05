FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

COPY web/requirements.txt /tmp/web-requirements.txt
COPY celery-worker/requirements.txt /tmp/celery-requirements.txt
COPY latest_model/requirements.txt /tmp/latest-model-requirements.txt
COPY shared_modules/requirements.txt /tmp/shared-modules-requirements.txt

RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir -r /tmp/web-requirements.txt && \
    pip install --no-cache-dir -r /tmp/celery-requirements.txt && \
    pip install --no-cache-dir -r /tmp/latest-model-requirements.txt && \
    pip install --no-cache-dir -r /tmp/shared-modules-requirements.txt

COPY . /app

EXPOSE 8000

CMD ["python", "web/manage.py", "runserver", "0.0.0.0:8000"]


