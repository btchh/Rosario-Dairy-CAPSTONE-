from rest_framework import serializers
from config.api_inputs import PositivePrimaryKeyRelatedField
from ..models import Customer, Order, OrderItem, Transaction
from inventory.models import Product
from inventory.serializers import ProductSerializer
from accounts.serializers import UserSerializer
from .customer_serializer import CustomerSerializer
from .transaction_serializer import TransactionNumberMixin, TransactionSerializer


class OrderItemSerializer(serializers.ModelSerializer):
    """Read-only — OrderItems are only ever created server-side inside
    place_order(), never posted to directly."""
    product = ProductSerializer(read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'quantity', 'unit_price', 'subtotal', 'total_amount']
        read_only_fields = fields


class OrderSerializer(serializers.ModelSerializer):
    customer = CustomerSerializer(read_only=True)
    customer_id = PositivePrimaryKeyRelatedField(
        queryset=Customer.objects.filter(is_active=True), source='customer', write_only=True
    )
    handled_by = UserSerializer(read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    transaction = TransactionSerializer(read_only=True)

    class Meta:
        model = Order
        fields = ['id', 'customer', 'customer_id', 'handled_by', 'status', 'warning', 'transaction', 'items', 'created_at', 'updated_at']
        read_only_fields = ['id', 'handled_by', 'status', 'warning', 'transaction', 'created_at', 'updated_at']

class OrderListProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ['id', 'name']


class OrderListItemSerializer(serializers.ModelSerializer):
    product = OrderListProductSerializer(read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'quantity', 'unit_price', 'subtotal', 'total_amount']


class OrderListTransactionSerializer(TransactionNumberMixin, serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = [
            'id', 'transaction_number', 'transaction_date', 'is_manually_encoded', 'recorded_cashier', 'can_update_payment', 'encoded_at', 'encoding_request_id',
            'subtotal', 'total_amount', 'payment_method', 'payment_status', 'payment_updated_at', 'payment_updated_by', 'payment_updated_by_user',
            'amount_tendered', 'change_due', 'source_reference', 'source_invoice_number',
        ]


class OrderListSerializer(OrderSerializer):
    items = OrderListItemSerializer(many=True, read_only=True)
    transaction = OrderListTransactionSerializer(read_only=True)
