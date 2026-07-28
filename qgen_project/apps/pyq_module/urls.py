from django.urls import path
from . import views

app_name = 'pyq_module'

urlpatterns = [
    path('',                            views.PYQModuleListView.as_view(),   name='list'),
    path('upload/',                     views.PYQModuleUploadView.as_view(), name='upload'),
    path('<int:pk>/',                   views.PYQModuleDetailView.as_view(), name='detail'),
    path('<int:pk>/delete/',            views.PYQModuleDeleteView.as_view(), name='delete'),
    path('<int:pk>/reextract/',         views.pyq_module_reextract,         name='reextract'),
    path('<int:pk>/status/',            views.pyq_module_status,             name='status'),
    path('question/<int:pk>/edit/',     views.QuestionUpdateView.as_view(),  name='question_edit'),
    path('question/<int:pk>/delete/',   views.QuestionDeleteView.as_view(),  name='question_delete'),
]
