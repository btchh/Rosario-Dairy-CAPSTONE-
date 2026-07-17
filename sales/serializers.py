# TODO: REFACTOR THIS TOO BABES
from rest_framework import serializers
from decimal import Decimal
from .models import Customer, Order, OrderItem, Transaction, TransactionItem
from inventory.models import Product, ProductBatch
from inventory.serializers import ProductSerializer, ProdBatchSerializer
from accounts.serializers import UserSerializer


class CustomerSerializer(serializers.ModelSerializer):
  class Meta:
    model = Customer
    fields = ['id', 'name', 'contact_number', 'email', 'address', 'created_by', 'created_at', 'updated_at']
    read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']


class OrderItemSerializer(serializers.ModelSerializer):
  product = ProductSerializer(read_only=True)
  product_id = serializers.PrimaryKeyRelatedField(
    queryset=Product.objects.all(), source='product', write_only=True
  )

  class Meta:
    model = OrderItem
    fields = ['id', 'product', 'product_id', 'quantity', 'unit_price', 'subtotal']
    read_only_fields = ['id', 'unit_price', 'subtotal']  # both system-computed, not client-supplied

  def create(self, validated_data):
    product = validated_data['product']
    quantity = validated_data['quantity']
    validated_data['unit_price'] = product.unit_price  # snapshot at order time
    validated_data['subtotal'] = quantity * product.unit_price
    return super().create(validated_data)


class OrderSerializer(serializers.ModelSerializer):
  customer = CustomerSerializer(read_only=True)
  customer_id = serializers.PrimaryKeyRelatedField(
    queryset=Customer.objects.all(), source='customer', write_only=True
  )
  handled_by = UserSerializer(read_only=True)
  items = OrderItemSerializer(many=True, read_only=True)

  class Meta:
    model = Order
    fields = ['id', 'customer', 'customer_id', 'handled_by', 'status', 'discount_type', 'discount_value', 'transaction', 'items', 'created_at', 'updated_at']
    read_only_fields = ['id', 'handled_by', 'status', 'transaction', 'created_at', 'updated_at']

  def validate(self, attrs):
    if self.instance is not None and ('discount_type' in attrs or 'discount_value' in attrs):
      raise serializers.ValidationError(
        "Discount cannot be changed after the order has been created."
      )

    discount_type = attrs.get('discount_type', 'none')
    discount_value = attrs.get('discount_value', Decimal('0.00'))
    if discount_type == 'percent' and not (0 <= discount_value <= 100):
      raise serializers.ValidationError("Percentage discount must be between 0 and 100.")
    if discount_type == 'fixed' and discount_value < 0:
      raise serializers.ValidationError("Discount value cannot be negative.")
    return attrs
  
  def update(self, instance, validated_data):
    # discount is a create-only decision, locked in at order placement — see
    # Round 7 handdown. Editing it later is intentionally blocked here, same
    # pattern as ProdBatchSerializer/IngBatchSerializer popping 'quantity'.
    validated_data.pop('discount_type', None)
    validated_data.pop('discount_value', None)
    return super().update(instance, validated_data)


class TransactionItemSerializer(serializers.ModelSerializer):
  product_batch = ProdBatchSerializer(read_only=True)

  class Meta:
    model = TransactionItem
    fields = ['id', 'product_batch', 'quantity', 'unit_price']
    read_only_fields = fields  # every field here is system-generated at checkout, never client-supplied


class TransactionSerializer(serializers.ModelSerializer):
  handled_by = UserSerializer(read_only=True)
  items = TransactionItemSerializer(many=True, read_only=True)

  class Meta:
    model = Transaction
    fields = ['id', 'handled_by', 'subtotal', 'discount_type', 'discount_value', 'discount_amount', 'total_amount', 'amount_tendered', 'change_due', 'payment_method', 'delivery_status', 'items', 'created_at']
    read_only_fields = ['id', 'handled_by', 'subtotal', 'discount_amount', 'total_amount', 'change_due', 'items', 'created_at']