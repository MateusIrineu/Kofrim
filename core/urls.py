from django.urls import path
from . import views

urlpatterns = [
    path('whatsapp/', views.whatsapp_webhook, name='whatsapp-webhook'),
]