from django.db import transaction as db_transaction
from decimal import Decimal
from django.utils import timezone
from ..models import Transaction, TransactionItem
from inventory.services.batch_service import BatchService
from .item_pricing import ZERO, money, price_items, reject_invoice_discount
from .payment_service import payment_values


def checkout(cart_items, staff_user, payment_method='cash',
             discount_type='none', discount_value=ZERO,
             amount_tendered=None, customer=None, payment_status='paid'):
    """Every cart line explicitly supplies its final selling price."""
    if payment_method not in ('cash', 'online'):
        raise ValueError(f"Invalid payment_method '{payment_method}'. Must be cash or online.")
    reject_invoice_discount(discount_type, discount_value)
    lines = price_items(cart_items, staff_user)
    subtotal = sum((line.subtotal for line in lines), ZERO)
    total = subtotal
    amount_tendered, change_due = payment_values(total, payment_method, payment_status, amount_tendered)

    with db_transaction.atomic():
        txn = Transaction.objects.create(
            handled_by=staff_user, customer=customer, payment_method=payment_method,
            subtotal=subtotal, total_amount=total,
            amount_tendered=amount_tendered, change_due=change_due,
            payment_status=payment_status, payment_updated_at=timezone.now(), payment_updated_by=staff_user,
        )
        # Lock products in a stable order, retaining the original encoded line numbers.
        for line_number, line in sorted(enumerate(lines, 1), key=lambda pair: pair[1].product.pk):
            consumed = BatchService.deduct_product_batch(line.product, line.quantity)
            quantity_so_far = gross_so_far = ZERO
            for batch, qty_taken in consumed:
                quantity_so_far += Decimal(str(qty_taken))
                cumulative_gross = money(quantity_so_far * line.unit_price)
                gross = cumulative_gross - gross_so_far
                sold_product = batch.product
                TransactionItem.objects.create(
                    transaction=txn, product_batch=batch, line_number=line_number,
                    quantity=qty_taken, unit_price=line.unit_price,
                    subtotal=gross, total_amount=gross,
                    product_id_snapshot=sold_product.pk,
                    product_name_snapshot=sold_product.name,
                    product_variant_snapshot=sold_product.variant,
                    category_id_snapshot=sold_product.category_id,
                    category_name_snapshot=sold_product.category.name,
                )
                gross_so_far = cumulative_gross
        return txn
