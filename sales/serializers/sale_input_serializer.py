from decimal import Decimal
from collections.abc import Mapping

from rest_framework import serializers

from inventory.models import Product
from config.api_inputs import require_item_list


class SaleItemInputSerializer(serializers.Serializer):
    product_id = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.filter(is_active=True),
        error_messages={'does_not_exist': 'Product not found or is inactive.'},
    )
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.00'))
    discount_type = serializers.ChoiceField(choices=['none', 'percent', 'fixed'], default='none')
    discount_value = serializers.DecimalField(max_digits=20, decimal_places=2, min_value=Decimal('0.00'), default=Decimal('0.00'))

    def get_fields(self):
        fields = super().get_fields()
        if self.context['request'].user.role == 'staff':
            fields['product_id'].queryset = Product.objects.filter(
                is_active=True, category__is_active=True, category__is_visible_to_staff=True,
            )
        return fields


def encoded_sale_items(raw_items, request):
    require_item_list(raw_items)
    serializer = SaleItemInputSerializer(data=raw_items, many=True, context={'request': request})
    if not serializer.is_valid():
        errors_by_item = serializer.errors
        entries = errors_by_item.items() if isinstance(errors_by_item, Mapping) else enumerate(errors_by_item)
        for index, errors in entries:
            if errors:
                message = '; '.join(f'{field}: {", ".join(map(str, messages))}' for field, messages in errors.items())
                raise ValueError(f'Item at index {index}: {message}')
    return [(item['product_id'], item['quantity'], item['unit_price'],
             item['discount_type'], item['discount_value']) for item in serializer.validated_data]
