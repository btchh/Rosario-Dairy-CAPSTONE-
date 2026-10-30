from config.api_inputs import object_body
from rest_framework import viewsets
from rest_framework.response import Response
from accounts.permissions import IsAdmin, IsStaff
from ..serializers import TransactionSerializer
from ..models import Customer
from ..services import SalesService
from ..serializers.sale_input_serializer import encoded_sale_items
from ..services.item_pricing import reject_discount_fields
from decimal import Decimal
from config.api_inputs import parse_decimal, parse_positive_id
from accounts.throttles import SaleWriteRateThrottle
from ..services.submission_service import request_id, submit_sale

class CheckoutView(viewsets.ViewSet):
    permission_classes = [IsAdmin | IsStaff]
    throttle_classes = [SaleWriteRateThrottle]

    @object_body
    def create(self, request):
        key = request_id(request)
        raw_items = request.data.get('items', [])
        payment_method = request.data.get('payment_method', 'cash')
        customer_id = request.data.get('customer_id')

        customer = None
        if customer_id not in (None, ''):
            try:
                customer_id = parse_positive_id(customer_id, 'customer_id')
                customer = Customer.objects.get(pk=customer_id, is_active=True)
            except (Customer.DoesNotExist, ValueError, TypeError):
                return Response({'error': 'Customer not found.'}, status=400)

        try:
            reject_discount_fields(request.data)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)

        amount_tendered = request.data.get('amount_tendered')
        if amount_tendered is not None:
            try:
                amount_tendered = parse_decimal(
                    amount_tendered, 'amount_tendered',
                    min_value=Decimal('0.00'), max_digits=20,
                )
            except ValueError as exc:
                return Response({'error': str(exc)}, status=400)

        try:
            cart_items = encoded_sale_items(raw_items, request)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)

        try:
            txn, created = submit_sale(user=request.user, key=key, endpoint='checkout',
                items=cart_items, customer=customer, payment_method=payment_method,
                amount_tendered=amount_tendered,
                create=lambda: SalesService.checkout(cart_items, request.user, payment_method,
                    amount_tendered=amount_tendered, customer=customer))
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        return Response(TransactionSerializer(txn).data, status=201 if created else 200)
