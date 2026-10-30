from django.urls import path, include, re_path
from rest_framework.routers import DefaultRouter
from . import views
from .views.retired_inventory_views import RetiredInventoryView

router = DefaultRouter()
router.register(r'products', views.ProductViewSet, basename='product')
router.register(r'product-batches', views.ProductBatchViewSet, basename='productbatch')
router.register(r'categories', views.CategoryViewSet)
router.register(r'stock-adjustments', views.StockAdjustmentViewSet, basename='stock-adjustment')
router.register(r'fefo-config', views.FEFOConfViewSet, basename='fefo-config')
router.register(r'stock-counts', views.StockCountViewSet, basename='stock-count')

urlpatterns = [
  # Empty legacy reads keep older product-only clients working during transition.
  re_path(r'^(?:ingredients|ingredient-batches|suppliers|low-stock/ingredients|expiring/ingredients)/(?P<legacy_detail>.*)$', RetiredInventoryView.as_view()),
  path('', include(router.urls)),
  path('low-stock/products/', views.LowStockProductView.as_view(), name='low-stock-products'),
  path('expiring/products/', views.ExpiringProductView.as_view(), name='expiring-products'),
]
