from django.urls import path
from . import views

app_name = 'pdf_module'

urlpatterns = [
    path('',                     views.PDFContextListView.as_view(),   name='list'),
    path('upload/',              views.PDFContextUploadView.as_view(), name='upload'),
    path('<uuid:pk>/',           views.PDFContextDetailView.as_view(), name='detail'),
    path('<uuid:pk>/delete/',    views.PDFContextDeleteView.as_view(), name='delete'),
    path('<uuid:pk>/status/',    views.pdf_context_status,             name='status'),
    path('<uuid:pk>/reindex/',   views.pdf_context_reindex,            name='reindex'),
]
