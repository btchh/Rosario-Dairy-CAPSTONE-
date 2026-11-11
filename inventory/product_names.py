"""Reviewed product aliases shared by catalog validation and sales imports.

Chocolate labels remain separate except for explicitly confirmed aliases.
An unspecified mozzarella size does not imply an explicitly sized product.
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
    'cara200': 'Cara 200ml',
    'cara200ml': 'Cara 200ml',
    'pandan200': 'Pandan200',
    'pandan200ml': 'Pandan200',
    'pandan300': 'Pandan300',
    'pandan300ml': 'Pandan300',
    'raw300': 'RAW300',
    'raw300ml': 'RAW300',
    'rawmilk300': 'Raw Milk 300ml',
    'rawmilk300ml': 'Raw Milk 300ml',
    'tm200': 'TM200',
    'tm200ml': 'TM200',
    'ube300': 'Ube 300ml',
    'ube300ml': 'Ube 300ml',
    'uns300': 'UNS300',
    'uns300ml': 'UNS300',
    'cmtm200': 'CMTM200',
    'cmtm200ml': 'CMTM200',
}


def canonical_product_name(value):
    return APPROVED_PRODUCT_NAMES.get(formatting_key(value), value)


def product_name_key(value):
    return formatting_key(canonical_product_name(value))
