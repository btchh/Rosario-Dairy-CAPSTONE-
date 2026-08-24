from typing import cast
from decimal import Decimal, InvalidOperation
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsAdmin
from inventory.models import Product
from ..models import Order
from ..serializers import OrderSerializer
from ..services import SalesService


class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.select_related('customer', 'handled_by', 'transaction').prefetch_related('items').all()
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def create(self, request, *args, **kwargs):
        serializer = OrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = cast(dict, serializer.validated_data)

        raw_items = request.data.get('items', [])
        if not raw_items:
            return Response({'error': 'No items provided.'}, status=400)

        items = []
        for index, entry in enumerate(raw_items):
            product_id = entry.get('product_id')
            quantity = entry.get('quantity')
            if product_id is None or quantity is None:
                return Response(
                    {'error': f"Item at index {index} requires 'product_id' and 'quantity'."}, status=400
                )
            try:
                quantity = Decimal(str(quantity))
            except InvalidOperation:
                return Response({'error': f"Invalid quantity for item at index {index}."}, status=400)
            if quantity <= 0:
                return Response(
                    {'error': f"Quantity for item at index {index} must be greater than zero."}, status=400
                )
            try:
                product = Product.objects.get(pk=product_id, is_active=True)
            except Product.DoesNotExist:
                return Response(
                    {'error': f"Product {product_id} at index {index} not found or is inactive."}, status=400
                )
            items.append((product, quantity))

        payment_method = request.data.get('payment_method', 'cash')
        amount_tendered = request.data.get('amount_tendered')
        if amount_tendered is not None:
            try:
                amount_tendered = Decimal(str(amount_tendered))
            except InvalidOperation:
                return Response({'error': 'amount_tendered must be a valid number.'}, status=400)

        try:
            order = SalesService.place_order(
                customer=validated_data['customer'],
                items=items,
                handled_by=request.user,
                discount_type=validated_data.get('discount_type', 'none'),
                discount_value=validated_data.get('discount_value', Decimal('0.00')),
                payment_method=payment_method,
                amount_tendered=amount_tendered,
            )
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        return Response(OrderSerializer(order).data, status=201)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsAdmin])
    def cancel(self, request, pk=None):
        order = self.get_object()
        if order.status == 'cancelled':
            return Response({'error': 'Order is already cancelled.'}, status=400)
        try:
            txn, skipped_batches = SalesService.void_fulfilled_order(order, request.user)
        except ValueError as e:
            return Response({'error': str(e)}, status=400)
        order.refresh_from_db()
        response_data = OrderSerializer(order).data
        if skipped_batches:
            response_data['warning'] = f"Stock could not be restored for the following expired/disposed batches: {', '.join(skipped_batches)}"
        return Response(response_data)