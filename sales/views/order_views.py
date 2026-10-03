from typing import cast
from decimal import Decimal
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from accounts.permissions import IsAdmin, IsStaff
from inventory.models import Product, ProductBatch
from ..models import Order, OrderItem, TransactionItem
from ..serializers import OrderSerializer
from ..serializers.order_serializer import OrderListSerializer
from ..services import SalesService
from config.api_inputs import parse_decimal, require_item_list


class OrderPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 500


class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    pagination_class = OrderPagination
    permission_classes = [IsAdmin | IsStaff]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_serializer_class(self):
        return OrderListSerializer if self.action == 'list' else OrderSerializer

    def get_queryset(self):
        if self.action == 'list':
            queryset = super().get_queryset().select_related(
                'customer', 'handled_by', 'transaction'
            ).prefetch_related(Prefetch(
                'items', queryset=OrderItem.objects.select_related('product')
            )).order_by('-created_at', '-id')
        else:
            available_batches = ProductBatch.objects.filter(
                status='available', expiration_date__gte=timezone.localdate()
            ).only('product_id', 'remaining_quantity')
            order_items = OrderItem.objects.select_related(
                'product__category'
            ).prefetch_related(Prefetch(
                'product__batches',
                queryset=available_batches,
                to_attr='available_batches_for_total',
            ))
            transaction_items = TransactionItem.objects.select_related(
                'product_batch__product__category'
            ).prefetch_related(Prefetch(
                'product_batch__product__batches',
                queryset=available_batches,
                to_attr='available_batches_for_total',
            ))
            queryset = super().get_queryset().select_related(
                'customer', 'handled_by', 'transaction__handled_by',
                'transaction__customer',
            ).prefetch_related(
                Prefetch('items', queryset=order_items),
                Prefetch('transaction__items', queryset=transaction_items),
            )
        if self.request.user.role == 'staff':
            queryset = queryset.exclude(
                items__product__category__is_visible_to_staff=False
            ).exclude(
                items__product__category__is_active=False
            ).exclude(
                transaction__items__product_batch__product__category__is_visible_to_staff=False
            ).exclude(
                transaction__items__product_batch__product__category__is_active=False
            )
        customer_id = self.request.query_params.get('customer_id')
        if customer_id:
            try:
                customer_id = int(customer_id)
            except ValueError as exc:
                raise ValidationError({
                    'customer_id': 'customer_id must be a valid customer id.'
                }) from exc
            queryset = queryset.filter(customer_id=customer_id)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = OrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = cast(dict, serializer.validated_data)

        raw_items = request.data.get('items', [])
        try:
            raw_items = require_item_list(raw_items)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)

        items = []
        for index, entry in enumerate(raw_items):
            product_id = entry.get('product_id')
            quantity = entry.get('quantity')
            if product_id is None or quantity is None:
                return Response(
                    {'error': f"Item at index {index} requires 'product_id' and 'quantity'."}, status=400
                )
            try:
                quantity = parse_decimal(
                    quantity, 'quantity', min_value=Decimal('0.01')
                )
            except ValueError as exc:
                return Response({'error': f'Item at index {index}: {exc}'}, status=400)
            try:
                products = Product.objects.filter(is_active=True)
                if request.user.role == 'staff':
                    products = products.filter(category__is_active=True,
                                               category__is_visible_to_staff=True)
                product = products.get(pk=product_id)
            except (Product.DoesNotExist, ValueError, TypeError):
                return Response(
                    {'error': f"Product {product_id} at index {index} not found or is inactive."}, status=400
                )
            items.append((product, quantity))

        payment_method = request.data.get('payment_method', 'cash')
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

    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def cancel(self, request, pk=None):
        order = self.get_object()
        if order.transaction and order.transaction.source_reference:
            return Response({'error': 'Imported historical orders cannot be cancelled.'}, status=400)
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
