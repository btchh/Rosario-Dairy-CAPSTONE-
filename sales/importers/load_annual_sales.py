"""Load parsed workbook sales into the linked catalog and sales tables."""

from collections import Counter, defaultdict
from datetime import datetime, time
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models import Users
from inventory.models import (
    Category, Product, ProductBatch, Ingredient, IngredientBatch, Supplier,
    StockAdjustment, StockCount,
)
from sales.models import Customer, Order, OrderItem, Transaction, TransactionItem

from .annual_sales import CENT, canonical_customer, customer_key, canonical_product, category_for_product


def sale_timestamp(sale_date):
    return timezone.make_aware(datetime.combine(sale_date, time(12, 0)))


def load(result, admin_username):
    tickets = [ticket for ticket in result.tickets
               if not ticket.cancelled and not ticket.duplicate_of and ticket.lines]
    if any(ticket.sale_date is None for ticket in tickets):
        raise ValueError('Cannot import tickets without a sale date.')
    if any(line.amount is None for ticket in tickets for line in ticket.lines
           if ticket.reference != '2023:NOV:597'):
        raise ValueError('Unresolved line amounts outside the audited Mauban invoice.')
    if len(result.monthly_checks) != 36 or any(
        abs(Decimal(check['difference'])) > Decimal('0.05')
        for check in result.monthly_checks
    ):
        raise ValueError('Workbook monthly totals do not reconcile within rounding tolerance.')
    owner = Users.objects.get(username=admin_username, role='admin', is_active=True)

    with db_transaction.atomic():
        protected_models = (Category, Product, ProductBatch, Ingredient,
                            IngredientBatch, Supplier, StockAdjustment, StockCount,
                            Customer, Order, OrderItem, Transaction, TransactionItem)
        if any(model.objects.exists() for model in protected_models):
            raise ValueError('Business tables are not empty; refusing to overwrite or mix imported history.')

        source_names = Counter(canonical_customer(ticket.customer_label)
                               for ticket in result.tickets if not ticket.duplicate_of)
        customer_names = {}
        for name, count in source_names.items():
            if name:
                key = name.casefold()
                if key not in customer_names or count > source_names[customer_names[key]]:
                    customer_names[key] = name
        Customer.objects.bulk_create([Customer(name=name, created_by=owner)
                                      for name in sorted(customer_names.values(), key=str.casefold)])
        customers = {customer.name.casefold(): customer for customer in Customer.objects.all()}

        catalog = {}
        for ticket in tickets:
            for line in ticket.lines:
                name = canonical_product(line.product)
                key = name.casefold()
                old = catalog.get(key)
                if old is None or (
                    line.unit_price is not None and
                    (old[3] is None or (ticket.sale_date, line.row) >= (old[1], old[2]))
                ):
                    catalog[key] = (name, ticket.sale_date, line.row, line.unit_price)
        categories = sorted({category_for_product(entry[0]) for entry in catalog.values()})
        Category.objects.bulk_create([Category(name=name) for name in categories])
        category_by_name = {category.name: category for category in Category.objects.all()}
        Product.objects.bulk_create([
            Product(name=name, category=category_by_name[category_for_product(name)],
                    unit='L' if name == 'Raw Milk 1L' else 'unit',
                    unit_price=price if price is not None and price >= 0 else Decimal('0.00'),
                    shelf_life=0, low_stock_threshold=0)
            for name, _, _, price in catalog.values()
        ], batch_size=500)
        products = {product.name.casefold(): product for product in Product.objects.select_related('category')}
        first_dates = {}
        for ticket in tickets:
            for line in ticket.lines:
                key = canonical_product(line.product).casefold()
                first_dates[key] = min(first_dates.get(key, ticket.sale_date), ticket.sale_date)
        ProductBatch.objects.bulk_create([
            ProductBatch(product=product, batch_number=f'HIST-{product.pk}',
                         initial_quantity=Decimal('0.00'), remaining_quantity=Decimal('0.00'),
                         date_received=first_dates[key], expiration_date=first_dates[key],
                         status='depleted', is_historical_reference=True)
            for key, product in products.items()
        ], batch_size=500)
        batches = {batch.product_id: batch for batch in ProductBatch.objects.all()}

        transactions = []
        for ticket in tickets:
            recorded_prices = sum((line.quantity * line.unit_price for line in ticket.lines
                                   if line.unit_price is not None), Decimal('0.00'))
            recorded_prices = recorded_prices.quantize(CENT, rounding=ROUND_HALF_UP)
            subtotal = max(recorded_prices, ticket.amount)
            discount = subtotal - ticket.amount
            unknown_prices = [line.product for line in ticket.lines if line.unit_price is None]
            notes = list(ticket.notes)
            if unknown_prices:
                notes.append('Unit prices not recorded: ' + ', '.join(dict.fromkeys(unknown_prices)))
            transactions.append(Transaction(
                handled_by=owner,
                customer=customers.get(customer_key(ticket.customer_label)),
                subtotal=subtotal, discount_type='fixed' if discount else 'none',
                discount_value=discount, discount_amount=discount,
                total_amount=ticket.amount, payment_method='unknown',
                source_reference=ticket.reference,
                source_invoice_number=ticket.invoice_number,
                source_customer_label=ticket.customer_label,
                source_note='; '.join(notes),
            ))
        Transaction.objects.bulk_create(transactions, batch_size=500)
        transaction_items = []
        orders = []
        order_items = []
        for ticket, sale in zip(tickets, transactions, strict=True):
            customer = customers.get(customer_key(ticket.customer_label))
            if customer:
                orders.append(Order(customer=customer, handled_by=owner, transaction=sale,
                                    discount_type=sale.discount_type,
                                    discount_value=sale.discount_value))
            for line in ticket.lines:
                product = products[canonical_product(line.product).casefold()]
                transaction_items.append(TransactionItem(
                    transaction=sale, product_batch=batches[product.pk],
                    quantity=line.quantity, unit_price=line.unit_price or Decimal('0.00'),
                    source_product_label=line.product, source_line_total=line.amount,
                    product_id_snapshot=product.pk, product_name_snapshot=product.name,
                    product_variant_snapshot=product.variant,
                    category_id_snapshot=product.category_id,
                    category_name_snapshot=product.category.name,
                ))
        TransactionItem.objects.bulk_create(transaction_items, batch_size=1000)
        Order.objects.bulk_create(orders, batch_size=500)
        order_by_transaction = {order.transaction_id: order for order in orders}
        for ticket, sale in zip(tickets, transactions, strict=True):
            order = order_by_transaction.get(sale.pk)
            if order is None:
                continue
            for line in ticket.lines:
                product = products[canonical_product(line.product).casefold()]
                order_items.append(OrderItem(
                    order=order, product=product, quantity=line.quantity,
                    unit_price=line.unit_price or Decimal('0.00'),
                    subtotal=line.amount or Decimal('0.00'),
                ))
        OrderItem.objects.bulk_create(order_items, batch_size=1000)

        for ticket, sale in zip(tickets, transactions, strict=True):
            sale.created_at = sale_timestamp(ticket.sale_date)
        Transaction.objects.bulk_update(transactions, ['created_at'], batch_size=500)
        date_by_transaction = {sale.pk: sale.created_at for sale in transactions}
        for order in orders:
            order.created_at = date_by_transaction[order.transaction_id]
            order.updated_at = order.created_at
        Order.objects.bulk_update(orders, ['created_at', 'updated_at'], batch_size=500)

        source_revenue = sum((ticket.amount for ticket in tickets), Decimal('0.00'))
        actual_revenue = Transaction.objects.aggregate(total=Sum('total_amount'))['total']
        if actual_revenue != source_revenue:
            raise ValueError(f'Revenue reconciliation failed: {actual_revenue} != {source_revenue}')
        expected = Counter(ticket.sale_date.year for ticket in tickets)
        if Transaction.objects.count() != len(tickets) or TransactionItem.objects.count() != len(transaction_items):
            raise ValueError('Imported row counts do not match parsed source records.')
        return {
            'transactions': len(transactions), 'transaction_items': len(transaction_items),
            'orders': len(orders), 'order_items': len(order_items),
            'customers': len(customers), 'products': len(products),
            'categories': len(categories), 'reference_batches': len(batches),
            'revenue': str(actual_revenue), 'years': dict(sorted(expected.items())),
        }


def merge_imported_customers(result):
    """Consolidate only customers from a completed historical import."""
    with db_transaction.atomic():
        if Transaction.objects.filter(source_reference__isnull=True).exists():
            raise ValueError('Live sales exist; refusing automatic customer merges.')
        source_names = Counter(canonical_customer(ticket.customer_label)
                               for ticket in result.tickets if not ticket.duplicate_of)
        preferred = {}
        for name, count in source_names.items():
            if name:
                key = name.casefold()
                if key not in preferred or count > source_names[preferred[key]]:
                    preferred[key] = name
        grouped = defaultdict(list)
        for customer in Customer.objects.all():
            key = customer_key(customer.name)
            if key in preferred:
                grouped[key].append(customer)
        merged = 0
        renamed = 0
        for key, group in grouped.items():
            group.sort(key=lambda customer: (customer.name == preferred[key],
                                             customer.transactions.count() + customer.orders.count()),
                       reverse=True)
            winner = group[0]
            for duplicate in group[1:]:
                Transaction.objects.filter(customer=duplicate).update(customer=winner)
                Order.objects.filter(customer=duplicate).update(customer=winner)
                duplicate.delete()
                merged += 1
            if winner.name != preferred[key]:
                winner.name = preferred[key]
                winner.save(update_fields=['name', 'updated_at'])
                renamed += 1
        return {'merged': merged, 'renamed': renamed, 'customers': Customer.objects.count()}
