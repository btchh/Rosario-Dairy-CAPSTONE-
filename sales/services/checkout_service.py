from django.db import transaction as db_transaction
from decimal import Decimal
from ..models import Transaction, TransactionItem
from inventory.services.batch_service import BatchService


MONEY_QUANTUM = Decimal('0.01')
MAX_MONEY = Decimal('999999999999999999.99')


def checkout(cart_items, staff_user, payment_method='cash',
            discount_type='none', discount_value=Decimal('0.00'),
            amount_tendered=None, customer=None):
    """
    cart_items: list of (product, quantity) OR (product, quantity, locked_price)
    tuples. When locked_price is supplied (e.g. an Order's snapshotted
    OrderItem.unit_price), it overrides the batch/product's live price, so
    a customer is charged what they were quoted at order time rather than
    whatever the price has drifted to by fulfillment.
    """
    valid_payment_methods = ['cash', 'online']
    if payment_method not in valid_payment_methods:
        raise ValueError(f"Invalid payment_method '{payment_method}'. Must be one of {valid_payment_methods}.")

    discount_value = Decimal(str(discount_value))
    if not discount_value.is_finite():
        raise ValueError("Discount value must be a finite number.")
    if discount_type == 'percent' and not (0 <= discount_value <= 100):
        raise ValueError("Percentage discount must be between 0 and 100.")
    if discount_type == 'fixed' and discount_value < 0:
        raise ValueError("Discount value cannot be negative.")
    if discount_type == 'none' and discount_value != Decimal('0.00'):
        raise ValueError("Discount value must be zero when discount type is none.")
    if discount_type not in ('none', 'percent', 'fixed'):
        raise ValueError(f"Invalid discount_type '{discount_type}'. Must be one of 'none', 'percent', 'fixed'.")

    if amount_tendered is not None:
        amount_tendered = Decimal(str(amount_tendered))
        if not amount_tendered.is_finite() or amount_tendered < 0 or amount_tendered > MAX_MONEY:
            raise ValueError("Amount tendered must be between 0 and 999999999999999999.99.")

    # Every checkout acquires product locks in the same order. This prevents
    # two multi-product carts submitted in opposite orders from deadlocking.
    cart_items = sorted(cart_items, key=lambda entry: entry[0].pk)

    with db_transaction.atomic():
        txn = Transaction.objects.create(
            handled_by=staff_user,
            customer=customer,
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
                price = Decimal(str(price))
                line_subtotal = (Decimal(str(qty_taken)) * price).quantize(MONEY_QUANTUM)
                if line_subtotal > MAX_MONEY or subtotal + line_subtotal > MAX_MONEY:
                    raise ValueError("Sale total exceeds the maximum supported amount.")
                sold_product = batch.product
                TransactionItem.objects.create(
                    transaction=txn,
                    product_batch=batch,
                    quantity=qty_taken,
                    unit_price=price,
                    product_id_snapshot=sold_product.pk,
                    product_name_snapshot=sold_product.name,
                    product_variant_snapshot=sold_product.variant,
                    category_id_snapshot=sold_product.category_id,
                    category_name_snapshot=sold_product.category.name,
                )
                subtotal += line_subtotal
        # --- discount ---
        if discount_type == 'percent':
            discount_amount = (
                subtotal * (discount_value / Decimal('100'))
            ).quantize(MONEY_QUANTUM)
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
