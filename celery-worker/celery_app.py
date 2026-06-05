import os
import sys
from pathlib import Path
from celery import Celery

parent_dir = Path(__file__).resolve().parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

app = Celery('rag_worker')

app.conf.broker_url = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
app.conf.result_backend = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
app.conf.accept_content = ['json']
app.conf.task_serializer = 'json'
app.conf.result_serializer = 'json'
app.conf.timezone = 'UTC'

import tasks

