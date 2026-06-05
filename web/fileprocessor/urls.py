from django.urls import path
from . import views

app_name = 'fileprocessor'

urlpatterns = [
    path('', views.index, name='index'),
    path('upload/', views.upload_and_process, name='upload_and_process'),
    path('result/', views.result, name='result'),
    path('result/download/', views.download_result, name='download_result'),
]

