"""Validate final selling prices for new sales; legacy stored amounts stay intact."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from config.api_inputs import parse_decimal

CENT = Decimal('0.01')
ZERO = Decimal('0.00')
MAX_MONEY = Decimal('999999999999999999.99')


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def reject_invoice_discount(discount_type='none', discount_value=ZERO):
    value = parse_decimal(discount_value, 'discount_value', min_value=ZERO, max_digits=20)
    if discount_type != 'none' or value != ZERO:
        raise ValueError('Discounts are no longer supported. Enter the final selling price.')


def reject_discount_fields(data):
    """Tolerate old clients' neutral defaults, never apply a new discount."""
    reject_invoice_discount(data.get('discount_type', 'none'), data.get('discount_value', ZERO))
    if 'discount_amount' in data:
        reject_invoice_discount('none', data['discount_amount'])
    if 'discount' in data or 'discounts' in data:
        raise ValueError('Discounts are no longer supported. Enter the final selling price.')


@dataclass(frozen=True)
class PricedItem:
    product: object
    quantity: Decimal
    unit_price: Decimal
    subtotal: Decimal
    total_amount: Decimal


def price_items(items, user):
    """Accept explicit final prices, retaining only neutral legacy tuple defaults."""
    if not items:
        raise ValueError('At least one item is required.')
    result, seen = [], set()
    for entry in items:
        if len(entry) not in (3, 5):
            raise ValueError('Every item requires an explicitly entered unit_price.')
        product, quantity, price = entry[:3]
        if product.pk in seen:
            raise ValueError('Combine quantities for duplicate products before submitting a cart.')
        seen.add(product.pk)
        quantity = parse_decimal(quantity, 'quantity', min_value=CENT)
        price = parse_decimal(price, 'unit_price', min_value=ZERO)
        kind, value = entry[3:] if len(entry) == 5 else ('none', ZERO)
        value = parse_decimal(value, 'discount_value', min_value=ZERO, max_digits=20)
        reject_invoice_discount(kind, value)
        subtotal = money(quantity * price)
        if subtotal > MAX_MONEY:
            raise ValueError('Sale total exceeds the maximum supported amount.')
        result.append(PricedItem(product, quantity, price, subtotal, subtotal))
    if sum((item.subtotal for item in result), ZERO) > MAX_MONEY:
        raise ValueError('Sale total exceeds the maximum supported amount.')
    return result
