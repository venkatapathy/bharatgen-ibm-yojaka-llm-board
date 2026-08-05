from django.urls import path
from . import views

app_name = 'pdf_module'

urlpatterns = [
    path('',                     views.PDFContextListView.as_view(),   name='list'),
    path('upload/',              views.PDFContextUploadView.as_view(), name='upload'),
    path('reindex-stale/',       views.reindex_stale_pdfs,             name='reindex_stale'),
    path('<uuid:pk>/',           views.PDFContextDetailView.as_view(), name='detail'),
    path('<uuid:pk>/file/',      views.pdf_context_file,               name='file'),
    path('<uuid:pk>/delete/',    views.PDFContextDeleteView.as_view(), name='delete'),
    path('<uuid:pk>/status/',    views.pdf_context_status,             name='status'),
    path('<uuid:pk>/chunks/',    views.pdf_context_chunks,             name='chunks'),
    path('<uuid:pk>/save-ocr/',  views.pdf_context_save_ocr,           name='save_ocr'),
    path('<uuid:pk>/reindex/',   views.pdf_context_reindex,            name='reindex'),
]
