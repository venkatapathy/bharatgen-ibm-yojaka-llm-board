from django.urls import path
from . import views

app_name = 'prompt_module'

urlpatterns = [
    path('',                              views.PromptListView.as_view(),   name='list'),
    path('create/',                       views.PromptCreateView.as_view(), name='create'),
    path('<int:pk>/',                     views.PromptDetailView.as_view(), name='detail'),
    path('<int:pk>/delete/',              views.PromptDeleteView.as_view(), name='delete'),
    path('<int:pk>/duplicate/',           views.prompt_duplicate,           name='duplicate'),
    path('<int:pk>/activate/',            views.prompt_activate,            name='activate'),
    path('<int:pk>/restore/<int:version>/', views.prompt_restore,           name='restore'),
]
