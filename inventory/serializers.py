from rest_framework import serializers
from .models import Product, ProductBatch, Ingredient, IngredientBatch, Category, Supplier, StockAdjustment, FEFOConf

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

# LowStockProds Serializer
class LowStockProductSerializer(serializers.Serializer):
  product = ProductSerializer()
  remaining_quantity = serializers.DecimalField(max_digits=10, decimal_places=2)

#LowStockIngs Serializer
class LowStockIngredientSerializer(serializers.Serializer):
  ingredient = IngredientSerializer()
  remaining_quantity = serializers.DecimalField(max_digits=10, decimal_places=2)

class CategorySerializer(serializers.Serializer):
  class Meta:
    model = Category
    fields = '__all__'

class SupplierSerializer(serializers.Serializer):
  class Meta:
    model = Supplier
    fields = '__all__'

class StockAdjustmentSerializer(serializers.ModelSerializer):
  class Meta:
    model = StockAdjustment
    fields = '__all__'

class FEFOConfSerializer(serializers.ModelSerializer):
  class Meta:
    model = FEFOConf
    fields = '__all__'