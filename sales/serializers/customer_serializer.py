from rest_framework import serializers
from ..models import Customer
from ..importers.annual_sales import canonical_customer


class CustomerSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        if 'name' not in attrs:
            return attrs
        name = canonical_customer(attrs['name'])
        if not name:
            raise serializers.ValidationError({'name': 'Enter a customer name.'})
        matches = Customer.objects.filter(name__iexact=name)
        if self.instance is not None:
            matches = matches.exclude(pk=self.instance.pk)
        for other in matches:
            # Different people can have the same full name. Preserve accounts
            # with conflicting contact information instead of inferring identity.
            distinct = False
            for field in ('contact_number', 'email', 'address'):
                incoming = attrs.get(field, getattr(self.instance, field, None))
                existing = getattr(other, field)
                if incoming and existing and ' '.join(incoming.split()).casefold() != ' '.join(existing.split()).casefold():
                    distinct = True
            if not distinct:
                raise serializers.ValidationError({'name': f'This customer already exists as {other.name} (ID {other.pk}). Select the existing account.'})
        attrs['name'] = name
        return attrs

    class Meta:
        model = Customer
        fields = ['id', 'name', 'contact_number', 'email', 'address', 'is_active', 'created_by', 'created_at', 'updated_at']
        read_only_fields = ['id', 'is_active', 'created_by', 'created_at', 'updated_at']


class CustomerSummarySerializer(CustomerSerializer):
    transaction_count = serializers.IntegerField(read_only=True)
    last_sale = serializers.DateTimeField(read_only=True, allow_null=True)
    # A customer's lifetime total can exceed the precision of one receipt.
    total_spent = serializers.DecimalField(max_digits=30, decimal_places=2, read_only=True)

    class Meta(CustomerSerializer.Meta):
        fields = CustomerSerializer.Meta.fields + ['transaction_count', 'last_sale', 'total_spent']
