from rest_framework import serializers
from django.utils import timezone
from ..models import Transaction, TransactionItem
from inventory.serializers import ProdBatchSerializer
from accounts.serializers import UserSerializer
from .customer_serializer import CustomerSerializer


class TransactionBatchSerializer(ProdBatchSerializer):
    """Serialize each batch once within a transaction response.

    Historical invoices repeatedly reference the same catalog batches. Keep
    the complete response shape while reusing their nested representations.
    This cache belongs to this serializer instance, never to another request.
    """
    def to_representation(self, instance):
        if not hasattr(self, '_batch_data'):
            self._batch_data = {}
        if instance.pk not in self._batch_data:
            if hasattr(instance, 'available_product_stock'):
                instance.product.available_stock = instance.available_product_stock
            self._batch_data[instance.pk] = super().to_representation(instance)
        return self._batch_data[instance.pk]


class TransactionItemSerializer(serializers.ModelSerializer):
    product_batch = TransactionBatchSerializer(read_only=True)

    class Meta:
        model = TransactionItem
        fields = [
            'id', 'product_batch', 'quantity', 'unit_price', 'line_number',
            'subtotal', 'total_amount',
            'source_product_label', 'source_line_total',
            'product_id_snapshot', 'product_name_snapshot',
            'product_variant_snapshot', 'category_id_snapshot',
            'category_name_snapshot',
        ]
        read_only_fields = fields  # every field here is system-generated at checkout, never client-supplied


class TransactionNumberMixin(serializers.Serializer):
    transaction_number = serializers.SerializerMethodField()
    transaction_date = serializers.SerializerMethodField()
    is_manually_encoded = serializers.SerializerMethodField()
    recorded_cashier = serializers.SerializerMethodField()
    can_update_payment = serializers.SerializerMethodField()
    payment_updated_by_user = UserSerializer(source='payment_updated_by', read_only=True)

    def get_can_update_payment(self, instance):
        return instance.encoded_at is not None and not instance.is_voided and instance.payment_status != 'paid'

    def get_recorded_cashier(self, instance):
        # Locally encoded sales use their processor in the Cashier column.
        # A workbook import owner does not identify the original cashier.
        return None if instance.source_reference else UserSerializer(instance.handled_by).data

    def get_transaction_number(self, instance):
        # Preserve recorded invoice formatting; missing numbers must not be fabricated.
        return instance.source_invoice_number or None

    def get_transaction_date(self, instance):
        return timezone.localtime(instance.created_at).date().isoformat() if instance.created_at else None

    def get_is_manually_encoded(self, instance):
        return instance.encoded_at is not None


class TransactionSerializer(TransactionNumberMixin, serializers.ModelSerializer):
    handled_by = UserSerializer(read_only=True)
    customer = CustomerSerializer(read_only=True)
    items = TransactionItemSerializer(many=True, read_only=True)

    class Meta:
        model = Transaction
        fields = ['id', 'transaction_number', 'transaction_date', 'is_manually_encoded', 'recorded_cashier', 'can_update_payment', 'encoded_at', 'encoding_request_id', 'handled_by', 'customer', 'subtotal', 'total_amount', 'amount_tendered', 'change_due', 'payment_method', 'payment_status', 'payment_updated_at', 'payment_updated_by', 'payment_updated_by_user', 'delivery_status', 'is_voided', 'voided_at', 'voided_by', 'void_reason', 'items', 'created_at', 'source_reference', 'source_invoice_number', 'source_customer_label', 'source_sales_segment', 'source_note']
        read_only_fields = ['id', 'handled_by', 'subtotal', 'total_amount', 'change_due', 'delivery_status', 'is_voided', 'voided_at', 'voided_by', 'void_reason', 'items', 'created_at', 'encoded_at', 'encoding_request_id', 'payment_status', 'payment_updated_at', 'payment_updated_by']
