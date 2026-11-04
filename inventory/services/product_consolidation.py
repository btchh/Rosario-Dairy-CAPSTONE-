"""Merge formatting duplicates and approved aliases, preserving sizes and variants."""

from collections import defaultdict

from django.db import transaction
from django.db.models import F, Q

from config.data_cache import invalidate_data_cache
from inventory.models import Product, ProductBatch
from inventory.product_names import canonical_product_name, product_name_key
from sales.models import OrderItem, TransactionItem


def duplicate_product_groups(product_ids=None):
    groups = defaultdict(list)
    products = Product.objects.select_related('category').order_by('id')
    if product_ids is not None:
        products = products.filter(pk__in=product_ids)
    for product in products:
        key = (product_name_key(product.name), product.unit.strip().casefold(),
               (product.variant or '').strip().casefold())
        groups[key].append(product)
    return [group for group in groups.values() if len(group) > 1]


def consolidation_plan(product_ids=None):
    plans = []
    for group in duplicate_product_groups(product_ids):
        keeper = group[0]  # Keep the oldest stable product ID and its category.
        label = canonical_product_name(min(group, key=lambda p: (len(p.name), p.pk)).name)
        plans.append({'keep_id': keeper.pk, 'name': label,
                      'members': [{'id': p.pk, 'name': p.name,
                                   'category_id': p.category_id} for p in group]})
    return plans


@transaction.atomic
def consolidate_products(product_ids=None):
    """Caller must prevent concurrent catalog/stock/sales writes (see command)."""
    plans = consolidation_plan(product_ids)
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
        invalidate_data_cache()
    return plans
