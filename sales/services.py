from django.db import transaction as db_transaction
from decimal import Decimal
from django.db.models import Sum, F
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth, TruncYear
from .models import Transaction, TransactionItem, Order, OrderItem
from inventory.models import ProductBatch
from inventory.services.batch_service import BatchService


class SalesService:

    @staticmethod
    def checkout(cart_items, staff_user, payment_method='cash',
                discount_type='none', discount_value=Decimal('0.00'),
                amount_tendered=None):
        """
        cart_items: list of (product, quantity) OR (product, quantity, locked_price)
        tuples. When locked_price is supplied (e.g. an Order's snapshotted
        OrderItem.unit_price), it overrides the batch/product's live price, so
        a customer is charged what they were quoted at order time rather than
        whatever the price has drifted to by fulfillment.
        """
        with db_transaction.atomic():
            txn = Transaction.objects.create(
                handled_by=staff_user,
                payment_method=payment_method,
                subtotal=Decimal('0.00'),
                total_amount=Decimal('0.00')
            )
            subtotal = Decimal('0.00')
            for entry in cart_items:
                if len(entry) == 3:
                    product, quantity, locked_price = entry
                else:
                    product, quantity = entry
                    locked_price = None

                consumed = BatchService.deduct_product_batch(product, quantity)
                for batch, qty_taken in consumed:
                    if locked_price is not None:
                        price = locked_price
                    else:
                        price = batch.unit_price if batch.unit_price is not None else batch.product.unit_price
                    TransactionItem.objects.create(
                        transaction=txn,
                        product_batch=batch,
                        quantity=qty_taken,
                        unit_price=price
                    )
                    subtotal += Decimal(str(qty_taken)) * price
            # --- discount ---
            if discount_type == 'percent':
                if not (0 <= discount_value <= 100):
                    raise ValueError("Percentage discount must be between 0 and 100.")
                discount_amount = subtotal * (discount_value / Decimal('100'))
            elif discount_type == 'fixed':
                if discount_value < 0:
                    raise ValueError("Discount value cannot be negative.")
                if discount_value > subtotal:
                    raise ValueError("Fixed discount cannot exceed the subtotal.")
                discount_amount = discount_value
            elif discount_type == 'none':
                discount_amount = Decimal('0.00')
            else:
                raise ValueError(f"Invalid discount_type '{discount_type}'. Must be one of 'none', 'percent', 'fixed'.")
            total = subtotal - discount_amount
            # --- cash tendered / change ---
            change_due = None
            if payment_method == 'cash':
                if amount_tendered is None:
                    raise ValueError("Amount tendered is required for cash payments.")
                if amount_tendered < total:
                    raise ValueError(f"Amount tendered ({amount_tendered}) is less than the total due ({total}).")
                change_due = amount_tendered - total
            txn.subtotal = subtotal
            txn.discount_type = discount_type
            txn.discount_value = discount_value
            txn.discount_amount = discount_amount
            txn.total_amount = total
            txn.amount_tendered = amount_tendered
            txn.change_due = change_due
            txn.save()
            return txn

    @staticmethod
    def fulfill_order(order, staff_user, payment_method='cash', amount_tendered=None):
        with db_transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
            if locked_order.status != 'confirmed':
                raise ValueError(f"Order must be 'confirmed' to fulfill, currently '{locked_order.status}'.")
            cart_items = [
                (item.product, item.quantity, item.unit_price)
                for item in locked_order.items.all()
            ]
            txn = SalesService.checkout(
                cart_items, staff_user, payment_method,
                discount_type=locked_order.discount_type,
                discount_value=locked_order.discount_value,
                amount_tendered=amount_tendered
            )
            locked_order.transaction = txn
            locked_order.status = 'fulfilled'
            locked_order.save()
        order.refresh_from_db()
        return txn
    
    @staticmethod
    def update_order_item(order, item_id, quantity):
        with db_transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
            if locked_order.status != 'placed':
                raise ValueError(f"Items can only be edited while order status is 'placed', currently '{locked_order.status}'.")

            if quantity <= Decimal('0.00'):
                raise ValueError("Quantity must be greater than zero.")

            try:
                item = OrderItem.objects.select_for_update().get(pk=item_id, order=locked_order)
            except (OrderItem.DoesNotExist, ValueError, TypeError):
                raise ValueError("Order item not found on this order.")

            item.quantity = quantity
            item.subtotal = quantity * item.unit_price
            item.save()
            return item

    @staticmethod
    def remove_order_item(order, item_id):
        with db_transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
            if locked_order.status != 'placed':
                raise ValueError(f"Items can only be removed while order status is 'placed', currently '{locked_order.status}'.")

            try:
                item = OrderItem.objects.select_for_update().get(pk=item_id, order=locked_order)
            except (OrderItem.DoesNotExist, ValueError, TypeError):
                raise ValueError("Order item not found on this order.")

            if locked_order.items.count() <= 1:
                raise ValueError("Cannot remove the last item on an order — cancel the order instead.")

            item.delete()
    
    @staticmethod
    def void_fulfilled_order(order, admin_user):
        if order.transaction is None:
            raise ValueError("This order has no linked transaction to void.")

        with db_transaction.atomic():
            txn = Transaction.objects.select_for_update().get(pk=order.transaction_id)
            if txn.is_voided:
                raise ValueError("This transaction has already been voided.")

            skipped_batches = []
            for item in txn.items.select_related('product_batch').all():
                batch = ProductBatch.objects.select_for_update().get(pk=item.product_batch_id) # pyright: ignore[reportAttributeAccessIssue]
                if batch.status in ('expired', 'disposed'):
                    skipped_batches.append(batch.batch_number)
                    continue
                batch.remaining_quantity += item.quantity
                if batch.status == 'depleted':
                    batch.status = 'available'
                batch.save()

            txn.is_voided = True
            txn.save()

            order.status = 'cancelled'
            order.save()

        return txn, skipped_batches

    PERIOD_TRUNC = {
        'daily': TruncDate,
        'weekly': TruncWeek,
        'monthly': TruncMonth,
        'yearly': TruncYear,
    }

    @staticmethod
    def get_revenue_report(period='monthly', start_date=None, end_date=None):
        """
        start_date/end_date are `date` objects (or None) and are inclusive on
        both ends, filtered against the transaction's created_at date.
        """
        trunc_fn = SalesService.PERIOD_TRUNC.get(period)
        if trunc_fn is None:
            raise ValueError(f"Invalid period '{period}'. Must be one of {list(SalesService.PERIOD_TRUNC)}.")

        qs = Transaction.objects.filter(is_voided=False)
        if start_date is not None:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date is not None:
            qs = qs.filter(created_at__date__lte=end_date)

        return (
            qs
            .annotate(bucket=trunc_fn('created_at'))
            .values('bucket')
            .annotate(total=Sum('total_amount'))
            .order_by('bucket')
        )

    @staticmethod
    def get_best_sellers(limit=10, start_date=None, end_date=None):
        qs = TransactionItem.objects.filter(transaction__is_voided=False)
        if start_date is not None:
            qs = qs.filter(transaction__created_at__date__gte=start_date)
        if end_date is not None:
            qs = qs.filter(transaction__created_at__date__lte=end_date)

        return (
            qs
            .values(
                product_name=F('product_batch__product__name'),
                product_variant=F('product_batch__product__variant')
            )
            .annotate(total_sold=Sum('quantity'))
            .order_by('-total_sold')[:limit]
        )
    
    @staticmethod
    def get_sales_by_category(start_date=None, end_date=None):
        qs = TransactionItem.objects.filter(transaction__is_voided=False)
        if start_date is not None:
            qs = qs.filter(transaction__created_at__date__gte=start_date)
        if end_date is not None:
            qs = qs.filter(transaction__created_at__date__lte=end_date)

        return (
            qs
            .values(category_name=F('product_batch__product__category__name'))
            .annotate(total_sold=Sum('quantity'))
            .order_by('-total_sold')
        )