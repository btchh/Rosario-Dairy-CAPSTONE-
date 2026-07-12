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
    fields = ['id', 'customer', 'customer_id', 'handled_by', 'status', 'transaction', 'items', 'created_at', 'updated_at']
    read_only_fields = ['id', 'handled_by', 'transaction', 'created_at', 'updated_at']


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
    fields = ['id', 'handled_by', 'total_amount', 'payment_method', 'delivery_status', 'items', 'created_at']
    read_only_fields = ['id', 'handled_by', 'total_amount', 'items', 'created_at']