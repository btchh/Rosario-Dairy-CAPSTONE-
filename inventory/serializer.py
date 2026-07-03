from rest_framework import serializers
from .models import Product, ProductBatch, Ingredient, IngredientBatch

# TODO: Look up how to create a proper serializer
# REMINDER: THIS IS TO BE USED IF REACT IS TO BE USED. OTHERWISE, **IGNORE THIS!**

# Product Serializer
class ProductSerializer(serializers.ModelSerializer):
  class Meta:
    model = Product
    fields = '__all__'

# Ingredient Serializer
class IngredientSerializer(serializers.ModelSerializer):
  class Meta:
    model = Ingredient
    fields = '__all__'

# ProductBatch Serializer
class ProdBatchSerializer(serializers.ModelSerializer):
  class Meta:
    model = ProductBatch
    fields = '__all__'

# Ingredient Serializer
class IngBatchSerializer(serializers.ModelSerializer):
  class Meta:
    model = IngredientBatch
    fields = '__all__'