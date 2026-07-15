from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from ..serializers import TransactionSerializer
from ..services import SalesService
from inventory.models import Product
from decimal import Decimal, InvalidOperation

class CheckoutView(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def create(self, request):
        raw_items = request.data.get('items', [])
        payment_method = request.data.get('payment_method', 'cash')
        discount_type = request.data.get('discount_type', 'none')

        try:
            discount_value = Decimal(str(request.data.get('discount_value', '0')))
        except InvalidOperation:
            return Response({'error': 'discount_value must be a valid number.'}, status=400)

        amount_tendered = request.data.get('amount_tendered')
        if amount_tendered is not None:
            try:
                amount_tendered = Decimal(str(amount_tendered))
            except InvalidOperation:
                return Response({'error': 'amount_tendered must be a valid number.'}, status=400)

        if not raw_items:
            return Response({'error': 'No items provided.'}, status=400)

        cart_items = []
        for entry in raw_items:
            product_id = entry.get('product_id')
            quantity = entry.get('quantity')
            if product_id is None or quantity is None:
                return Response({'error': "Each item requires 'product_id' and 'quantity'."}, status=400)

            try:
                quantity = Decimal(str(quantity))
            except InvalidOperation:
                return Response({'error': f"Invalid quantity for product {product_id}."}, status=400)

            if quantity <= 0:
                return Response({'error': f"Quantity for product {product_id} must be greater than zero."}, status=400)

            try:
                product = Product.objects.get(pk=product_id, is_active=True)
            except Product.DoesNotExist:
                return Response({'error': f"Product {product_id} not found or is inactive."}, status=400)
            cart_items.append((product, quantity))

        try:
            txn = SalesService.checkout(cart_items, request.user, payment_method,
                            discount_type, discount_value, amount_tendered)
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        return Response(TransactionSerializer(txn).data, status=201)