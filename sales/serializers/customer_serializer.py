from rest_framework import serializers
from ..models import Customer


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ['id', 'name', 'contact_number', 'email', 'address', 'is_active', 'created_by', 'created_at', 'updated_at']
        read_only_fields = ['id', 'is_active', 'created_by', 'created_at', 'updated_at']


class CustomerSummarySerializer(CustomerSerializer):
    transaction_count = serializers.IntegerField(read_only=True)
    last_sale = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta(CustomerSerializer.Meta):
        fields = CustomerSerializer.Meta.fields + ['transaction_count', 'last_sale']
