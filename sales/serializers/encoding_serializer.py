from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from sales.models import Customer


class EncodingInputSerializer(serializers.Serializer):
    request_id = serializers.UUIDField()
    transaction_date = serializers.DateField(input_formats=['%Y-%m-%d'])
    customer_id = serializers.PrimaryKeyRelatedField(
        source='customer', queryset=Customer.objects.filter(is_active=True),
        required=False, allow_null=True,
    )
    si_number = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    note = serializers.CharField(max_length=2000, required=False, allow_blank=True, default='')
    payment_method = serializers.ChoiceField(choices=['cash', 'online'], default='cash')
    payment_status = serializers.ChoiceField(choices=['pending', 'paid'], default='pending')
    amount_tendered = serializers.DecimalField(
        max_digits=20, decimal_places=2, min_value=Decimal('0.00'),
        required=False, allow_null=True,
    )

    def validate_transaction_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError('Transaction date cannot be in the future.')
        return value


class EncodingPaymentSerializer(serializers.Serializer):
    payment_status = serializers.ChoiceField(choices=['pending', 'paid'])
    payment_method = serializers.ChoiceField(choices=['cash', 'online'], required=False)
    amount_tendered = serializers.DecimalField(
        max_digits=20, decimal_places=2, min_value=Decimal('0.00'), required=False, allow_null=True,
    )

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError('Only payment_status, payment_method and amount_tendered may be updated here.')
        return attrs
