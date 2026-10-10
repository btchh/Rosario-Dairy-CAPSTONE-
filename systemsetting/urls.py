from django.urls import path

from .views import NotificationSettingsView, SettingsOverviewView, SystemSettingsView
from .views.inbox_views import NotificationInboxView
from .views.inventory_views import InventorySettingsView


urlpatterns = [
    path('inbox/', NotificationInboxView.as_view(), name='notification-inbox'),
    path('', SettingsOverviewView.as_view(), name='settings-overview'),
    path('system/', SystemSettingsView.as_view(), name='system-settings'),
    path('inventory/', InventorySettingsView.as_view(), name='inventory-settings'),
    path('notifications/', NotificationSettingsView.as_view(), name='notification-settings'),
    path('system/<int:pk>/', SystemSettingsView.as_view(), name='system-settings-detail'),
    path(
        'notifications/<int:pk>/',
        NotificationSettingsView.as_view(),
        name='notification-settings-detail',
    ),
]
