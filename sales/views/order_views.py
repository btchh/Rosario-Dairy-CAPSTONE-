from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction as db_transaction
from decimal import Decimal, InvalidOperation
from ..models import Order
from ..serializers import OrderSerializer, OrderItemSerializer
from ..services import SalesService
from ..permissions import IsAdminOrReadOnlyCancel

class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.select_related('customer', 'handled_by').prefetch_related('items').all()
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def perform_create(self, serializer):
        serializer.save(handled_by=self.request.user)

    @action(detail=True, methods=['post'])
    def items(self, request, pk=None):
        order = self.get_object()
        with db_transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
            if locked_order.status != 'placed':
                return Response(
                    {'error': f"Items can only be added while order status is 'placed', currently '{locked_order.status}'."},
                    status=400
                )
            item_serializer = OrderItemSerializer(data=request.data)
            item_serializer.is_valid(raise_exception=True)
            item_serializer.save(order=locked_order)

        order.refresh_from_db()
        return Response(OrderSerializer(order).data, status=201)
    
    @action(detail=True, methods=['patch', 'delete'], url_path='items/(?P<item_pk>[^/.]+)')
    def item_detail(self, request, pk=None, item_pk=None):
        order = self.get_object()

        if request.method == 'PATCH':
            quantity = request.data.get('quantity')
            if quantity is None:
                return Response({'error': "'quantity' is required."}, status=400)
            try:
                quantity = Decimal(str(quantity))
            except InvalidOperation:
                return Response({'error': 'quantity must be a valid number.'}, status=400)
            try:
                SalesService.update_order_item(order, item_pk, quantity)
            except ValueError as e:
                return Response({'error': str(e)}, status=400)

        else:  # DELETE
            try:
                SalesService.remove_order_item(order, item_pk)
            except ValueError as e:
                return Response({'error': str(e)}, status=400)

        order.refresh_from_db()
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsAdminOrReadOnlyCancel])
    def confirm(self, request, pk=None):
        order = self.get_object()
        self.check_object_permissions(request, order)
        with db_transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
            if locked_order.status != 'placed':
                return Response({'error': f"Order must be 'placed' to confirm, currently '{locked_order.status}'."}, status=400)
            locked_order.status = 'confirmed'
            locked_order.save()
        order.refresh_from_db()
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=['post'])
    def fulfill(self, request, pk=None):
        order = self.get_object()
        payment_method = request.data.get('payment_method', 'cash')
        discount_type = request.data.get('discount_type', 'none')
        amount_tendered = request.data.get('amount_tendered')

        try:
            discount_value = Decimal(str(request.data.get('discount_value', '0')))
        except InvalidOperation:
            return Response({'error': 'discount_value must be a valid number.'}, status=400)

        if amount_tendered is not None:
            try:
                amount_tendered = Decimal(str(amount_tendered))
            except InvalidOperation:
                return Response({'error': 'amount_tendered must be a valid number.'}, status=400)

        try:
            SalesService.fulfill_order(order, request.user, payment_method, amount_tendered,
                                        discount_type, discount_value)
        except ValueError as e:
            return Response({'error': str(e)}, status=400)
        order.refresh_from_db()
        return Response(OrderSerializer(order).data)
        order = self.get_object()
        self.check_object_permissions(request, order)

        with db_transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)

            if locked_order.status == 'cancelled':
                return Response({'error': "Order is already cancelled."}, status=400)

            if locked_order.status == 'fulfilled':
                try:
                    txn, skipped_batches = SalesService.void_fulfilled_order(locked_order, request.user)
                except ValueError as e:
                    return Response({'error': str(e)}, status=400)
                order.refresh_from_db()
                response_data = OrderSerializer(order).data
                if skipped_batches:
                    response_data['warning'] = f"Stock could not be restored for the following expired/disposed batches: {', '.join(skipped_batches)}"
                return Response(response_data)

            locked_order.status = 'cancelled'
            locked_order.save()

        order.refresh_from_db()
        return Response(OrderSerializer(order).data)