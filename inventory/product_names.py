"""Product aliases approved for consolidation on 2026-11-04.

Chocolate labels are deliberately separate. An unspecified mozzarella size
does not imply any of the explicitly sized mozzarella products.
"""

import re


def formatting_key(value):
    # Keep punctuation that can distinguish sizes, flavors, and packaging.
    return re.sub(r'[\s()]', '', value or '').casefold()


APPROVED_PRODUCT_NAMES = {
    'melon200': 'MELON200',
    'melon200ml': 'MELON200',
    'melon300': 'Melon 300ml',
    'melon300ml': 'Melon 300ml',
    'mozza': 'Mozza',
    'mozzarella': 'Mozza',
}


def canonical_product_name(value):
    return APPROVED_PRODUCT_NAMES.get(formatting_key(value), value)


def product_name_key(value):
    return formatting_key(canonical_product_name(value))
