from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('organizations', views.OrganizationViewSet,    basename='organization')
router.register('users',         views.UserViewSet,            basename='user')
router.register('pdf/contexts',  views.PDFContextViewSet,      basename='pdf-context')
router.register('pyq/modules',   views.PYQModuleViewSet,       basename='pyq-module')
router.register('questions',     views.QuestionViewSet,        basename='question')
router.register('prompts',       views.PromptTemplateViewSet,  basename='prompt')
router.register('generate/runs', views.BatchRunViewSet,        basename='batch-run')

urlpatterns = [
    path('', include(router.urls)),
    path('auth/', include('rest_framework.urls', namespace='rest_framework')),
]
