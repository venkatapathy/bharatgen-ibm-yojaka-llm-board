import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'qgen.settings.development')

app = Celery('qgen')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
