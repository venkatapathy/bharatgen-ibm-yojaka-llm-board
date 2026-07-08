from .base import *

DEBUG = True

INSTALLED_APPS += ['django_extensions']

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

CSRF_TRUSTED_ORIGINS = [
    'http://localhost:8001',
    'http://127.0.0.1:8001',
    'http://10.129.6.47:8001',
]
