from django.db import transaction as db_transaction
from decimal import Decimal
from django.db.models import Sum, F
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth, TruncYear
from .models import Transaction, TransactionItem
from inventory.services.batch_service import BatchService

class SalesService:

    @staticmethod
    def checkout(cart_items, staff_user, payment_method='cash',
                discount_type='none', discount_value=Decimal('0.00'),
                amount_tendered=None):
        with db_transaction.atomic():
            txn = Transaction.objects.create(
                handled_by=staff_user,
                payment_method=payment_method,
                subtotal=Decimal('0.00'),
                total_amount=Decimal('0.00')
            )

            subtotal = Decimal('0.00')
            for product, quantity in cart_items:
                consumed = BatchService.deduct_product_batch(product, quantity)
                for batch, qty_taken in consumed:
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
                if discount_value > subtotal:
                    raise ValueError("Fixed discount cannot exceed the subtotal.")
                discount_amount = discount_value
            else:
                discount_amount = Decimal('0.00')

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
    def fulfill_order(order, staff_user, payment_method='cash'):
        """
        Routes a confirmed Order through the same checkout logic,
        then links the resulting Transaction back to the Order.
        """
        if order.status != 'confirmed':
            raise ValueError(f"Order must be 'confirmed' to fulfill, currently '{order.status}'.")

        cart_items = [(item.product, item.quantity) for item in order.items.all()]
        txn = SalesService.checkout(cart_items, staff_user, payment_method)

        order.transaction = txn
        order.status = 'fulfilled'
        order.save()
        return txn

    @staticmethod
    def void_fulfilled_order(order, admin_user):
        if order.transaction is None:
            raise ValueError("This order has no linked transaction to void.")

        txn = order.transaction
        if txn.is_voided:
            raise ValueError("This transaction has already been voided.")

        with db_transaction.atomic():
            skipped_batches = []
            for item in txn.items.all():
                batch = item.product_batch
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
    def get_revenue_report(period='monthly'):
        trunc_fn = SalesService.PERIOD_TRUNC.get(period)
        if trunc_fn is None:
            raise ValueError(f"Invalid period '{period}'. Must be one of {list(SalesService.PERIOD_TRUNC)}.")

        return (
            Transaction.objects
            .filter(is_voided=False)
            .annotate(bucket=trunc_fn('created_at'))
            .values('bucket')
            .annotate(total=Sum('total_amount'))
            .order_by('bucket')
        )

    @staticmethod
    def get_best_sellers(limit=10):
        return (
            TransactionItem.objects
            .filter(transaction__is_voided=False)
            .values(
                product_name=F('product_batch__product__name'),
                product_variant=F('product_batch__product__variant')
            )
            .annotate(total_sold=Sum('quantity'))
            .order_by('-total_sold')[:limit]
        )
    
    @staticmethod
    def get_sales_by_category():
        return (
            TransactionItem.objects
            .filter(transaction__is_voided=False)
            .values(category_name=F('product_batch__product__category__name'))
            .annotate(total_sold=Sum('quantity'))
            .order_by('-total_sold')
        )