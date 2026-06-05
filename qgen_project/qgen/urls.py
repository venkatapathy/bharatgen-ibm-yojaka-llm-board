from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('',          include('apps.core.urls')),
    path('pdf/',      include('apps.pdf_module.urls')),
    path('pyq/',      include('apps.pyq_module.urls')),
    path('prompts/',  include('apps.prompt_module.urls')),
    path('generate/', include('apps.question_generation.urls')),
    path('api/',      include('apps.api.urls')),
    path('admin/',    admin.site.urls),
    path('accounts/', include('allauth.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
