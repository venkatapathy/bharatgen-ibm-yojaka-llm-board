from .base import *

DEBUG = True

INSTALLED_APPS += ['django_extensions']

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

CSRF_TRUSTED_ORIGINS = [
    'http://localhost:8001',
    'http://127.0.0.1:8001',
    'http://10.129.7.47:8001',
    'https://*.trycloudflare.com',
    'https://*.ngrok-free.dev',
    'https://*.ngrok-free.app',
    'https://*.ngrok.io',
]

# Public tunnels (cloudflared / ngrok) terminate TLS upstream
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True
