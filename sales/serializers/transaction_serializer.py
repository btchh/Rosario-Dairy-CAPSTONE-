from rest_framework import serializers
from ..models import Transaction, TransactionItem
from inventory.serializers import ProdBatchSerializer
from accounts.serializers import UserSerializer
from .customer_serializer import CustomerSerializer


class TransactionItemSerializer(serializers.ModelSerializer):
    product_batch = ProdBatchSerializer(read_only=True)

    class Meta:
        model = TransactionItem
        fields = ['id', 'product_batch', 'quantity', 'unit_price']
        read_only_fields = fields  # every field here is system-generated at checkout, never client-supplied


class TransactionSerializer(serializers.ModelSerializer):
    handled_by = UserSerializer(read_only=True)
    customer = CustomerSerializer(read_only=True)
    items = TransactionItemSerializer(many=True, read_only=True)

    class Meta:
        model = Transaction
        fields = ['id', 'handled_by', 'customer', 'subtotal', 'discount_type', 'discount_value', 'discount_amount', 'total_amount', 'amount_tendered', 'change_due', 'payment_method', 'delivery_status', 'items', 'created_at']
        read_only_fields = ['id', 'handled_by', 'subtotal', 'discount_amount', 'total_amount', 'change_due', 'items', 'created_at']
