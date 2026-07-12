from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'products', views.ProductViewSet, basename='product')
router.register(r'ingredients', views.IngredientViewSet, basename='ingredient')
router.register(r'product-batches', views.ProductBatchViewSet, basename='productbatch')
router.register(r'ingredient-batches', views.IngredientBatchViewSet, basename='ingredientbatch')
router.register(r'categories', views.CategoryViewSet)
router.register(r'suppliers', views.SupplierViewSet)
router.register(r'stock-adjustments', views.StockAdjustmentViewSet, basename='stock-adjustment')
router.register(r'fefo-config', views.FEFOConfViewSet, basename='fefo-config')

urlpatterns = [
  path('', include(router.urls)),
  path('low-stock/products/', views.LowStockProductView.as_view(), name='low-stock-products'),
  path('low-stock/ingredients/', views.LowStockIngredientView.as_view(), name='low-stock-ingredients'),
  path('expiring/products/', views.ExpiringProductView.as_view(), name='expiring-products'),
  path('expiring/ingredients/', views.ExpiringIngredientView.as_view(), name='expiring-ingredients'),
]