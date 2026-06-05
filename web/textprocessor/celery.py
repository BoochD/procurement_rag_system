"""
Celery configuration for textprocessor project.
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from celery import Celery

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'textprocessor.settings')

app = Celery('textprocessor')

app.config_from_object('django.conf:settings', namespace='CELERY')

app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')

