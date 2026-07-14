from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from ..models import Order
from ..serializers import OrderSerializer
from ..services import SalesService
from ..permissions import IsAdminOrReadOnlyCancel

class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.select_related('customer', 'handled_by').prefetch_related('items').all()
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(handled_by=self.request.user)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsAdminOrReadOnlyCancel])
    def confirm(self, request, pk=None):
        order = self.get_object()
        self.check_object_permissions(request, order)
        if order.status != 'placed':
            return Response({'error': f"Order must be 'placed' to confirm, currently '{order.status}'."}, status=400)
        order.status = 'confirmed'
        order.save()
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=['post'])
    def fulfill(self, request, pk=None):
        order = self.get_object()
        payment_method = request.data.get('payment_method', 'cash')
        try:
            SalesService.fulfill_order(order, request.user, payment_method)
        except ValueError as e:
            return Response({'error': str(e)}, status=400)
        order.refresh_from_db()
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsAdminOrReadOnlyCancel])
    def cancel(self, request, pk=None):
        order = self.get_object()
        self.check_object_permissions(request, order)

        if order.status == 'cancelled':
            return Response({'error': "Order is already cancelled."}, status=400)

        if order.status == 'fulfilled':
            try:
                txn, skipped_batches = SalesService.void_fulfilled_order(order, request.user)
            except ValueError as e:
                return Response({'error': str(e)}, status=400)
            order.refresh_from_db()
            response_data = OrderSerializer(order).data
            if skipped_batches:
                response_data['warning'] = f"Stock could not be restored for the following expired/disposed batches: {', '.join(skipped_batches)}"
            return Response(response_data)

        order.status = 'cancelled'
        order.save()
        return Response(OrderSerializer(order).data)