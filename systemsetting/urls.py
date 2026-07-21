from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'system', views.SystemSettingsViewSet, basename='system-settings')
router.register(r'notifications', views.NotificationSettingsViewSet, basename='notification-settings')

urlpatterns = [
  path('', include(router.urls)),
]