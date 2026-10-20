"""Merge formatting duplicates without combining sizes, flavors, or variants."""

import re
from collections import defaultdict

from django.db import transaction
from django.db.models import F, Q

from config.data_cache import invalidate_data_cache
from inventory.models import Product, ProductBatch
from sales.models import OrderItem, TransactionItem


def product_name_key(value):
    # Retain punctuation such as hyphens, decimal points and slashes: these may
    # distinguish flavors or package sizes. Parentheses only wrap label text.
    return re.sub(r'[\s()]', '', value or '').casefold()


def duplicate_product_groups():
    groups = defaultdict(list)
    for product in Product.objects.select_related('category').order_by('id'):
        key = (product_name_key(product.name), product.unit.strip().casefold(),
               (product.variant or '').strip().casefold())
        groups[key].append(product)
    return [group for group in groups.values() if len(group) > 1]


def consolidation_plan():
    plans = []
    for group in duplicate_product_groups():
        keeper = group[0]  # Keep the oldest stable product ID and its category.
        label = min(group, key=lambda p: (len(p.name), p.pk)).name
        plans.append({'keep_id': keeper.pk, 'name': label,
                      'members': [{'id': p.pk, 'name': p.name,
                                   'category_id': p.category_id} for p in group]})
    return plans


@transaction.atomic
def consolidate_products():
    """Caller must prevent concurrent catalog/stock/sales writes (see command)."""
    plans = consolidation_plan()
    for plan in plans:
        keeper = Product.objects.get(pk=plan['keep_id'])
        ids = [member['id'] for member in plan['members']]
        removed = [pk for pk in ids if pk != keeper.pk]
        lines = TransactionItem.objects.filter(
            Q(product_id_snapshot__in=ids) |
            Q(product_id_snapshot__isnull=True, product_batch__product_id__in=ids))
        # Keep original sale labels, including ones entered outside the importer.
        lines.filter(source_product_label='', product_name_snapshot__isnull=False).update(
            source_product_label=F('product_name_snapshot'))
        lines.update(product_id_snapshot=keeper.pk, product_name_snapshot=plan['name'],
                     category_id_snapshot=keeper.category_id,
                     category_name_snapshot=keeper.category.name)
        plan['batches_moved'] = ProductBatch.objects.filter(product_id__in=removed).update(product=keeper)
        plan['order_items_moved'] = OrderItem.objects.filter(product_id__in=removed).update(product=keeper)
        Product.objects.filter(pk=keeper.pk).update(
            name=plan['name'], is_active=Product.objects.filter(pk__in=ids, is_active=True).exists())
        Product.objects.filter(pk__in=removed).delete()
    if plans:
        transaction.on_commit(invalidate_data_cache)
    return plans
