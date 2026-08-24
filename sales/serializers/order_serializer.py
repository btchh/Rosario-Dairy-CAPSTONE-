from rest_framework import serializers
from decimal import Decimal
from ..models import Customer, Order, OrderItem
from inventory.serializers import ProductSerializer
from accounts.serializers import UserSerializer
from .customer_serializer import CustomerSerializer
from .transaction_serializer import TransactionSerializer


class OrderItemSerializer(serializers.ModelSerializer):
    """Read-only — OrderItems are only ever created server-side inside
    place_order(), never posted to directly."""
    product = ProductSerializer(read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'quantity', 'unit_price', 'subtotal']
        read_only_fields = fields


class OrderSerializer(serializers.ModelSerializer):
    customer = CustomerSerializer(read_only=True)
    customer_id = serializers.PrimaryKeyRelatedField(
        queryset=Customer.objects.all(), source='customer', write_only=True
    )
    handled_by = UserSerializer(read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    transaction = TransactionSerializer(read_only=True)

    class Meta:
        model = Order
        fields = ['id', 'customer', 'customer_id', 'handled_by', 'status', 'discount_type', 'discount_value', 'transaction', 'items', 'created_at', 'updated_at']
        read_only_fields = ['id', 'handled_by', 'status', 'transaction', 'created_at', 'updated_at']

    def validate(self, attrs):
        discount_type = attrs.get('discount_type', 'none')
        discount_value = attrs.get('discount_value', Decimal('0.00'))
        if discount_type == 'percent' and not (0 <= discount_value <= 100):
            raise serializers.ValidationError("Percentage discount must be between 0 and 100.")
        if discount_type == 'fixed' and discount_value < 0:
            raise serializers.ValidationError("Discount value cannot be negative.")
        return attrs