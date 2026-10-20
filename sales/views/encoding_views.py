from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import action

from accounts.throttles import SaleWriteRateThrottle
from config.api_inputs import object_body
from sales.serializers.encoding_serializer import EncodingInputSerializer, EncodingPaymentSerializer
from sales.serializers.sale_input_serializer import encoded_sale_items
from sales.services.encoding_service import EncodingConflict, encode_transaction
from sales.services.item_pricing import reject_invoice_discount
from sales.services.payment_service import update_encoding_payment
from .transaction_views import TransactionViewSet


class EncodingViewSet(TransactionViewSet):
    """Staff/admin manual encoding; records remain shared with all sales consumers."""
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        return super().get_queryset().filter(encoded_at__isnull=False)

    def get_throttles(self):
        return [SaleWriteRateThrottle()] if self.request.method in ('POST', 'PATCH') else []

    @action(detail=True, methods=['patch'])
    @object_body
    def payment(self, request, pk=None):
        sale = self.get_object()
        values = EncodingPaymentSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        try:
            sale = update_encoding_payment(sale.pk, request.user, **values.validated_data)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(sale).data)

    @object_body
    def create(self, request):
        metadata = EncodingInputSerializer(data=request.data)
        metadata.is_valid(raise_exception=True)
        try:
            reject_invoice_discount(request.data.get('discount_type', 'none'), request.data.get('discount_value', '0'))
            items = encoded_sale_items(request.data.get('items', []), request)
            sale, created = encode_transaction(
                items=items, handled_by=request.user, **metadata.validated_data,
            )
        except EncodingConflict as exc:
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(sale).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
