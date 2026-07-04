from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'products', views.ProductViewSet, basename='product')
router.register(r'ingredients', views.IngredientViewSet, basename='ingredient')
router.register(r'product-batches', views.ProductBatchViewSet, basename='productbatch')
router.register(r'ingredient-batches', views.IngredientBatchViewSet, basename='ingredientbatch')

urlpatterns = [
  path('', include(router.urls))
]