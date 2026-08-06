from django.urls import path
from . import views

app_name = 'question_generation'

urlpatterns = [
    path('',                     views.BatchRunListView.as_view(),   name='list'),
    path('new/',                 views.BatchRunNewView.as_view(),    name='new'),
    path('<int:pk>/',            views.BatchRunDetailView.as_view(), name='detail'),
    path('<int:pk>/review/',     views.batch_run_review,             name='review'),
    path('<int:pk>/delete/',     views.BatchRunDeleteView.as_view(), name='delete'),
    path('<int:pk>/status/',     views.batch_run_status,             name='status'),
    path('<int:pk>/export/',     views.batch_run_export,             name='export'),
    path('<int:pk>/rerun/',      views.batch_run_rerun,              name='rerun'),
    path('<int:pk>/regenerate/', views.batch_run_regenerate,         name='regenerate'),
    path('question/<int:pk>/edit/', views.GeneratedQuestionUpdateView.as_view(), name='question_edit'),
    path('question/<int:pk>/delete/', views.GeneratedQuestionDeleteView.as_view(), name='question_delete'),
    path('question/<int:pk>/decision/', views.generated_question_decision, name='question_decision'),
]
