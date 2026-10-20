from django.db import transaction as db_transaction
from decimal import Decimal
from ..models import Transaction, TransactionItem
from inventory.services.batch_service import BatchService
from .item_pricing import ZERO, money, price_items, reject_invoice_discount
from config.api_inputs import parse_decimal


def checkout(cart_items, staff_user, payment_method='cash',
             discount_type='none', discount_value=ZERO,
             amount_tendered=None, customer=None):
    """Every cart line explicitly supplies its selling price and optional discount."""
    if payment_method not in ('cash', 'online'):
        raise ValueError(f"Invalid payment_method '{payment_method}'. Must be cash or online.")
    reject_invoice_discount(discount_type, discount_value)
    lines = price_items(cart_items, staff_user)
    subtotal = sum((line.subtotal for line in lines), ZERO)
    discount_amount = sum((line.discount_amount for line in lines), ZERO)
    total = subtotal - discount_amount
    if amount_tendered is not None:
        amount_tendered = parse_decimal(amount_tendered, 'amount_tendered', min_value=ZERO, max_digits=20)
    change_due = None
    if payment_method == 'cash':
        if amount_tendered is None:
            raise ValueError('Amount tendered is required for cash payments.')
        if amount_tendered < total:
            raise ValueError(f'Amount tendered ({amount_tendered}) is less than the total due ({total}).')
        change_due = amount_tendered - total

    with db_transaction.atomic():
        txn = Transaction.objects.create(
            handled_by=staff_user, customer=customer, payment_method=payment_method,
            subtotal=subtotal, discount_amount=discount_amount, total_amount=total,
            amount_tendered=amount_tendered, change_due=change_due,
        )
        # Lock products in a stable order, retaining the original encoded line numbers.
        for line_number, line in sorted(enumerate(lines, 1), key=lambda pair: pair[1].product.pk):
            consumed = BatchService.deduct_product_batch(line.product, line.quantity)
            quantity_so_far = gross_so_far = discount_so_far = ZERO
            for batch, qty_taken in consumed:
                quantity_so_far += Decimal(str(qty_taken))
                cumulative_gross = money(quantity_so_far * line.unit_price)
                cumulative_discount = money(line.discount_amount * cumulative_gross / line.subtotal) if line.subtotal else ZERO
                gross = cumulative_gross - gross_so_far
                discount = cumulative_discount - discount_so_far
                sold_product = batch.product
                TransactionItem.objects.create(
                    transaction=txn, product_batch=batch, line_number=line_number,
                    quantity=qty_taken, unit_price=line.unit_price,
                    discount_type=line.discount_type,
                    discount_value=discount if line.discount_type == 'fixed' else line.discount_value,
                    subtotal=gross, discount_amount=discount, total_amount=gross - discount,
                    product_id_snapshot=sold_product.pk,
                    product_name_snapshot=sold_product.name,
                    product_variant_snapshot=sold_product.variant,
                    category_id_snapshot=sold_product.category_id,
                    category_name_snapshot=sold_product.category.name,
                )
                gross_so_far, discount_so_far = cumulative_gross, cumulative_discount
        return txn
