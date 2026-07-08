from rest_framework import serializers
from .models import Product, ProductBatch, Ingredient, IngredientBatch, Category, Supplier, StockAdjustment, FEFOConf
from django.db.models import Sum

# TODO: Refactor Serializers
# REMEMBER: IF serializer.ModelSerializer, you fix those, else leave it as is
class CategorySerializer(serializers.ModelSerializer):
  class Meta:
    model = Category
    fields = '__all__'

class ProductSerializer(serializers.ModelSerializer):
  category = CategorySerializer(read_only=True)
  category_id = serializers.PrimaryKeyRelatedField(
    queryset=Category.objects.all(),
    source='category',
    write_only=True
  )
  total_stock = serializers.SerializerMethodField()

  def get_total_stock(self, obj):
    return obj.batches.filter(status='available').aggregate(
      total=Sum('remaining_quantity')
    )['total'] or 0
  
  class Meta:
    model = Product
    fields = [
      'id', 'name', 'variant', 'unit', 'unit_price', 'shelf_life', 'low_stock_threshold', 'is_active', 'category', 'category_id', 'total_stock', 'created_at', 'updated_at'
    ]
    read_only_fields = ['id', 'created_at', ' updated_at', 'total_stock']
class IngredientSerializer(serializers.ModelSerializer):
  total_stock = serializers.SerializerMethodField()

  def get_total_stock(self, obj):
    return obj.batches.filter(status='available'). aggregate(
      total=Sum('remaining_quantity')
    )['total'] or 0

  class Meta:
    model = Ingredient
    fields = [
      'id', 'name', 'unit', 'unit_price', 'shelf_life', 'ingredient_type', 'grade','low_stock_threshold', 'is_active', 'created_at', 'updated_at', 'total_stock'
    ]
    read_only_fields = ['id', 'created_at', 'updated_at', 'total_stock']

class ProductBatchSerializer(serializers.ModelSerializer):
  product = ProductSerializer(read_only=True)
  product_id = serializers.PrimaryKeyRelatedField(
    queryset=Product.objects.all(),
    source='product',
    write_only=True
  )
  class Meta:
    model = ProductBatch
    fields = [
      'id', 'product', 'batch_number', 'initial_quantity', 'remaining_quantity', 'expiration_date', 'date_received', 'status', 'notes', 'created_at', 'updated_at'
    ]
    read_only_fields = ['id', 'batch_number', 'created_at', 'updated_at']

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


class SupplierSerializer(serializers.ModelSerializer):
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