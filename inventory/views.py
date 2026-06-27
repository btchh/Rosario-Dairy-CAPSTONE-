from django.shortcuts import render
from django.views import View
from .models import Product, ProductBatch, Ingredient, IngredientBatch
from .services import batch_service

# Create your views here.

# Product List
class GetActiveProds(View):
  def get(self, request):
    products = Product.objects.filter(is_active=True)
    return render(request, 'inventory/products/list.html', {'products': products})