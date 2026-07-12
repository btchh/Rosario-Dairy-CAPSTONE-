from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from ..serializers import TransactionSerializer
from ..services import checkout
from inventory.models import Product

class CheckoutView(viewsets.ViewSet):
    """
    Direct walk-in sale — not tied to an Order at all.
    POST body: {"items": [{"product_id": 1, "quantity": 5}, ...], "payment_method": "cash"}
    """
    permission_classes = [IsAuthenticated]

    def create(self, request):
        raw_items = request.data.get('items', [])
        payment_method = request.data.get('payment_method', 'cash')

        if not raw_items:
            return Response({'error': 'No items provided.'}, status=400)

        cart_items = []
        for entry in raw_items:
            try:
                product = Product.objects.get(pk=entry['product_id'])
            except Product.DoesNotExist:
                return Response({'error': f"Product {entry.get('product_id')} not found."}, status=400)
            cart_items.append((product, entry['quantity']))

        try:
            txn = checkout(cart_items, request.user, payment_method)
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        return Response(TransactionSerializer(txn).data, status=status.HTTP_201_CREATED)