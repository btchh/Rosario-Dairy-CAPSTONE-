from config.api_inputs import object_body
from rest_framework import viewsets, status
from rest_framework.response import Response
from accounts.permissions import IsAdmin, IsStaff
from ..serializers import TransactionSerializer
from ..models import Customer
from ..services import SalesService
from inventory.models import Product
from decimal import Decimal
from config.api_inputs import parse_decimal, require_item_list

class CheckoutView(viewsets.ViewSet):
    permission_classes = [IsAdmin | IsStaff]

    @object_body
    def create(self, request):
        raw_items = request.data.get('items', [])
        payment_method = request.data.get('payment_method', 'cash')
        discount_type = request.data.get('discount_type', 'none')
        customer_id = request.data.get('customer_id')

        customer = None
        if customer_id not in (None, ''):
            try:
                customer = Customer.objects.get(pk=customer_id)
            except (Customer.DoesNotExist, ValueError, TypeError):
                return Response({'error': 'Customer not found.'}, status=400)

        try:
            discount_value = parse_decimal(
                request.data.get('discount_value', '0'), 'discount_value',
                max_digits=20,
            )
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
            raw_items = require_item_list(raw_items)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)

        cart_items = []
        for entry in raw_items:
            product_id = entry.get('product_id')
            quantity = entry.get('quantity')
            if product_id is None or quantity is None:
                return Response({'error': "Each item requires 'product_id' and 'quantity'."}, status=400)

            try:
                quantity = parse_decimal(
                    quantity, 'quantity', min_value=Decimal('0.01')
                )
            except ValueError as exc:
                return Response({'error': f'Product {product_id}: {exc}'}, status=400)

            try:
                products = Product.objects.filter(is_active=True)
                if request.user.role == 'staff':
                    products = products.filter(category__is_visible_to_staff=True)
                product = products.get(pk=product_id)
            except (Product.DoesNotExist, ValueError, TypeError):
                return Response({'error': f"Product {product_id} not found or is inactive."}, status=400)
            cart_items.append((product, quantity))

        try:
            txn = SalesService.checkout(cart_items, request.user, payment_method,
                            discount_type, discount_value, amount_tendered,
                            customer=customer)
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        return Response(TransactionSerializer(txn).data, status=201)
