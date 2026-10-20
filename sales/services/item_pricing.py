"""Validate explicit selling prices and calculate discounts per encoded line."""
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
        raise ValueError('Apply discounts to individual items, not the whole transaction.')


@dataclass(frozen=True)
class PricedItem:
    product: object
    quantity: Decimal
    unit_price: Decimal
    discount_type: str
    discount_value: Decimal
    subtotal: Decimal
    discount_amount: Decimal
    total_amount: Decimal


def price_items(items, user):
    """Accept (product, quantity, price[, discount_type, discount_value]) tuples."""
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
        if kind not in ('none', 'percent', 'fixed'):
            raise ValueError('Item discount_type must be none, percent, or fixed.')
        if kind == 'none' and value != ZERO:
            raise ValueError('Item discount_value must be zero when discount_type is none.')
        if kind == 'percent' and value > 100:
            raise ValueError('Percentage discount must be between 0 and 100.')
        if user.role != 'admin' and value != ZERO:
            raise ValueError('Only an admin can authorize a price discount.')
        subtotal = money(quantity * price)
        discount = money(subtotal * value / 100) if kind == 'percent' else value if kind == 'fixed' else ZERO
        if discount > subtotal:
            raise ValueError('Fixed item discount cannot exceed the line subtotal.')
        if subtotal > MAX_MONEY:
            raise ValueError('Sale total exceeds the maximum supported amount.')
        result.append(PricedItem(product, quantity, price, kind, value, subtotal, discount, subtotal - discount))
    if sum((item.subtotal for item in result), ZERO) > MAX_MONEY:
        raise ValueError('Sale total exceeds the maximum supported amount.')
    return result
