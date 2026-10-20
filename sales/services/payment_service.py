"""Record payment state without repeating the sale or touching inventory."""
from django.db import transaction
from django.utils import timezone

from config.api_inputs import parse_decimal
from sales.models import Transaction
from .item_pricing import ZERO


def payment_values(total, method, status, amount_tendered):
    if method not in ('cash', 'online'):
        raise ValueError(f"Invalid payment_method '{method}'. Must be cash or online.")
    if status not in ('pending', 'paid'):
        raise ValueError('payment_status must be pending or paid.')
    if amount_tendered is not None:
        amount_tendered = parse_decimal(amount_tendered, 'amount_tendered', min_value=ZERO, max_digits=20)
    if status == 'pending':
        if amount_tendered not in (None, ZERO):
            raise ValueError('Pending payments cannot include an amount tendered. Partial payments are not supported.')
        return None, None
    if method == 'cash':
        if amount_tendered is None:
            raise ValueError('Amount tendered is required for cash payments.')
        if amount_tendered < total:
            raise ValueError(f'Amount tendered ({amount_tendered}) is less than the total due ({total}).')
        return amount_tendered, amount_tendered - total
    return amount_tendered, None


@transaction.atomic
def update_encoding_payment(sale_id, user, *, payment_status, **details):
    sale = Transaction.objects.select_for_update().get(pk=sale_id)
    if sale.encoded_at is None:
        raise ValueError('Only manually encoded transactions can use this payment update.')
    if sale.is_voided:
        raise ValueError('Payment cannot be updated on a voided transaction.')
    method = details.get('payment_method', sale.payment_method)
    tendered = details.get('amount_tendered', sale.amount_tendered if sale.payment_status == 'paid' and payment_status == 'paid' else None)
    tendered, change = payment_values(sale.total_amount, method, payment_status, tendered)
    if (sale.payment_status, sale.payment_method, sale.amount_tendered, sale.change_due) == (payment_status, method, tendered, change):
        return sale
    sale.payment_status = payment_status
    sale.payment_method = method
    sale.amount_tendered = tendered
    sale.change_due = change
    sale.payment_updated_at = timezone.now()
    sale.payment_updated_by = user
    sale.save(update_fields=['payment_status', 'payment_method', 'amount_tendered', 'change_due', 'payment_updated_at', 'payment_updated_by'])
    return sale
