"""Move clear customer aliases to a canonical account without changing receipts."""
from collections import defaultdict

from django.db import transaction
from django.db.models import Count
from config.data_cache import invalidate_data_cache
from sales.customer_names import GROUPS
from sales.importers.annual_sales import canonical_customer, customer_key
from sales.models import Customer, Order, Transaction

FIELDS = ('contact_number', 'email', 'address')


def normalized_contact(value):
    return ' '.join((value or '').split()).casefold()


def consolidation_plan():
    groups = defaultdict(list)
    for customer in Customer.objects.annotate(sale_count=Count('transactions')).order_by('pk'):
        key = customer_key(customer.name)
        if key:
            groups[key].append(customer)
    result, review = [], []
    for key, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        canonical = canonical_customer(members[0].name)
        identities = {c.name.strip().casefold() for c in members}
        conflicts = [field for field in FIELDS
                     if len({normalized_contact(getattr(c, field)) for c in members if getattr(c, field)}) > 1]
        same_contact = any(len({normalized_contact(getattr(c, field)) for c in members if getattr(c, field)}) == 1
                           and all(getattr(c, field) for c in members) for field in ('contact_number', 'email'))
        details = [{'id': c.pk, 'name': c.name, 'transactions': c.sale_count} for c in members]
        if conflicts or (len(identities) == 1 and canonical not in GROUPS and not same_contact):
            review.append({'members': details, 'reason': 'conflicting_contact_details' if conflicts else 'same_name_identity_unconfirmed',
                           'conflicting_fields': conflicts})
            continue
        # Retain an existing canonical display identity; otherwise the oldest ID.
        keeper = min(members, key=lambda c: (c.name != canonical, c.pk))
        result.append({'keep_id': keeper.pk, 'name': canonical, 'members': details,
                       'contacts': {field: getattr(keeper, field) or next((getattr(c, field) for c in members
                                    if getattr(c, field)), None) for field in FIELDS}})
    return {'merge_groups': result, 'needs_review': review}


@transaction.atomic
def consolidate_customers(plan=None):
    """Caller must lock customer/order/receipt writes (management command)."""
    plan = plan or consolidation_plan()
    for group in plan['merge_groups']:
        keeper = Customer.objects.get(pk=group['keep_id'])
        ids = [c['id'] for c in group['members']]
        removed = [pk for pk in ids if pk != keeper.pk]
        # Record exact old links in the private manifest. Preserve all receipt
        # labels, SI numbers, dates, amounts, payment states and classifications.
        group['old_transaction_links'] = list(Transaction.objects.filter(customer_id__in=removed).values('id', 'customer_id'))
        group['old_order_links'] = list(Order.objects.filter(customer_id__in=removed).values('id', 'customer_id'))
        group['transactions_moved'] = Transaction.objects.filter(customer_id__in=removed).update(customer=keeper)
        group['orders_moved'] = Order.objects.filter(customer_id__in=removed).update(customer=keeper)
        Customer.objects.filter(pk=keeper.pk).update(name=group['name'], **group['contacts'],
            is_active=Customer.objects.filter(pk__in=ids, is_active=True).exists())
        Customer.objects.filter(pk__in=removed).delete()
    if plan['merge_groups']:
        invalidate_data_cache()
    return plan
