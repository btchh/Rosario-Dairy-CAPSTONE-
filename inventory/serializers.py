from rest_framework import serializers
from .models import Product, ProductBatch, Ingredient, IngredientBatch, Category, Supplier, StockAdjustment, FEFOConf
from accounts.serializers import UserSerializer
from django.db.models import Sum

# TODO: Refactor Serializers
# REMEMBER: IF serializer.ModelSerializer, you fix those, else leave it as is
class CategorySerializer(serializers.ModelSerializer):
  class Meta:
    model = Category
    fields = [
      'id', 'name', 'description', 'is_active', 'created_at', 'updated_at'
    ]
    read_only_fields = ['id', 'created_at', 'updated_at']

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
    read_only_fields = ['id', 'created_at', 'updated_at', 'total_stock']
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

class ProdBatchSerializer(serializers.ModelSerializer):
  product = ProductSerializer(read_only=True)
  product_id = serializers.PrimaryKeyRelatedField(
    queryset=Product.objects.all(),
    source='product',
    write_only=True
  )
  quantity = serializers.DecimalField(
    max_digits = 10,
    decimal_places = 2, 
    write_only = True
  )
  class Meta:
    model = ProductBatch
    fields = [
      'id', 'product', 'product_id', 'batch_number', 'initial_quantity', 'remaining_quantity', 'quantity', 'expiration_date', 'date_received', 'status', 'notes', 'created_at', 'updated_at'
    ]
    read_only_fields = ['id', 'batch_number', 'quantity', 'initial_quantity', 'remaining_quantity','created_at', 'updated_at']

class IngBatchSerializer(serializers.ModelSerializer):
  ingredient = IngredientSerializer(read_only=True)
  ingredient_id = serializers.PrimaryKeyRelatedField(
    queryset=Ingredient.objects.all(),
    source='ingredient',
    write_only=True
  ) 
  quantity = serializers.DecimalField(
    max_digits = 10,
    decimal_places = 2, 
    write_only = True
  )

  class Meta:
    model = IngredientBatch
    fields = [
      'id', 'ingredient', 'ingredient_id','batch_number', 'supplier', 'unit_price', 'initial_quantity', 'remaining_quantity', 'quantity','expiration_date', 'date_received', 'status', 'notes', 'created_at', 'updated_at'
    ]
    read_only_fields = ['id', 'batch_number', 'quantity', 'initial_quantity', 'remaining_quantity','created_at', 'updated_at']

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
    fields = [
      'id', 'name', 'contact_number', 'address', 'is_active', 'created_at','updated_at'
    ]
    read_only_fields = ['id', 'created_at', 'updated_at']

class StockAdjustmentSerializer(serializers.ModelSerializer):
  product_batch = ProdBatchSerializer(read_only=True)
  product_batch_id = serializers.PrimaryKeyRelatedField(    
    queryset=ProductBatch.objects.all(),
    source='product_batch',
    write_only = True,
    required = False,
    allow_null = True
  )
  ingredient_batch = IngBatchSerializer(read_only=True)
  ingredient_batch_id = serializers.PrimaryKeyRelatedField(
    queryset = IngredientBatch.objects.all(),
    source = 'ingredient_batch',
    write_only = True,
    required = False,
    allow_null = True
  )

  adjusted_by = UserSerializer(read_only=True)

  class Meta:
    model = StockAdjustment
    fields = [
      'id', 'product_batch', 'product_batch_id','ingredient_batch', 'ingredient_batch_id','adjustment_type', 'quantity', 'unit_cost', 'reason', 'adjusted_by', 'created_at', 'updated_at'
      ]
    read_only_fields = ['id', 'adjusted_by', 'created_at', 'updated_at']
class FEFOConfSerializer(serializers.ModelSerializer):
  class Meta:
    model = FEFOConf
    fields = [
      'id', 'near_expiry_threshold', 'low_stock_threshold', 'updated_at'
    ]
    read_only_fields = ['id', 'updated_at']