"""Approved aliases retain receipts, sizes, and excluded chocolate identities."""

from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.db.models import Sum
from django.test import SimpleTestCase
from django.utils import timezone

from config.test_support import APITestCase
from inventory.models import Category, Product, ProductBatch
from inventory.product_names import canonical_product_name, product_name_key
from inventory.serializers import ProductSerializer
from inventory.services.product_consolidation import consolidation_plan, consolidate_products
from sales.importers.annual_sales import canonical_product
from sales.importers.refresh_annual_sales import POLICY_PATH, product_key
from sales.models import Customer, Order, OrderItem, Transaction, TransactionItem


class ReviewedProductNameTests(SimpleTestCase):
    def test_imports_and_catalog_share_only_the_approved_equivalences(self):
        groups = {
            'MELON200': ['MELON200', 'Melon 200ml', 'Melon 200'],
            'Melon 300ml': ['MELON300', 'Melon 300ml', 'Melon 300'],
            'Mozza': ['Mozza', 'Mozzarella'],
            'Cara 200ml': ['CARA200', 'Cara 200ml'],
            'Pandan200': ['Pandan200', 'Pandan 200ml'],
            'Pandan300': ['Pandan300', 'Pandan 300ml'],
            'RAW300': ['RAW300', 'Raw300ml'],
            'Raw Milk 300ml': ['Raw Milk300', 'Raw Milk 300ml'],
            'TM200': ['TM200', 'TM200ml'],
            'Ube 300ml': ['UBE300', 'Ube 300ml'],
            'UNS300': ['UNS300', 'UNS 300ml'],
            'CMTM200': ['CMTM200', 'CMTM200ML', 'CMTM 200ml'],
        }
        policy = json.loads(POLICY_PATH.read_text())
        for canonical, aliases in groups.items():
            for alias in aliases:
                self.assertEqual(canonical_product_name(alias), canonical)
                self.assertEqual(canonical_product(alias), canonical)
                self.assertEqual(product_name_key(alias), product_name_key(canonical))
                self.assertEqual(policy['products'][product_key(alias)]['decision'], 'dairy')

    def test_chocolate_packaging_sizes_and_punctuation_remain_distinct(self):
        names = ['Choco L', 'Choco LTR', 'CML', 'Choco Pouch', 'CML-TM',
                 'Melon 200ml', 'Melon 300ml', 'MelonL', 'Melon Pouch',
                 'Mozza', 'Mozza 1kg', 'Mozza 200g', 'Mozza 2kg',
                 'WC1.60kg', 'WC160kg']
        names += ['CMTM200', 'CMTM300', 'CM200', 'CM200ml',
                  'TM200', 'TM300', 'RAW300', 'Raw Milk300']
        self.assertEqual(len({product_name_key(name) for name in names}), len(names))
        for name in [*names[:5], 'CMTM200', 'CMTM300', 'CM200', 'CM200ml']:
            self.assertEqual(canonical_product_name(name), name)


class ReviewedProductConsolidationTests(APITestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Alias review dairy')
        self.admin = get_user_model().objects.create_user(
            username='alias-review', email='alias-review@example.test', role='admin')
        self.customer = Customer.objects.create(name='Alias customer', created_by=self.admin)
        self.pairs = [(self.product(a), self.product(b), label) for a, b, label in [
            ('MELON200', 'Melon 200ml', 'MELON200'),
            ('MELON300', 'Melon 300ml', 'Melon 300ml'),
            ('Mozza', 'Mozzarella', 'Mozza'),
            ('CARA200', 'Cara 200ml', 'Cara 200ml'),
            ('Pandan200', 'Pandan 200ml', 'Pandan200'),
            ('Pandan300', 'Pandan 300ml', 'Pandan300'),
            ('RAW300', 'Raw300ml', 'RAW300'),
            ('Raw Milk300', 'Raw Milk 300ml', 'Raw Milk 300ml'),
            ('TM200', 'TM200ml', 'TM200'),
            ('UBE300', 'Ube 300ml', 'Ube 300ml'),
            ('UNS300', 'UNS 300ml', 'UNS300'),
            ('CMTM200', 'CMTM200ML', 'CMTM200'),
        ]]
        self.selected = [p.pk for a, b, _ in self.pairs for p in (a, b)]
        self.excluded = [self.product(name) for name in [
            'Choco L', 'Choco LTR', 'CML', 'MelonL', 'Melon Pouch',
            'CMTM300', 'CML-TM', 'CM200', 'CM200ml',
            'Mozza 1kg', 'Mozza 200g', 'Mozza 2kg',
            'BB300', 'BB 300',
        ]]

    def product(self, name, **overrides):
        return Product.objects.create(name=name, category=self.category,
                                      **{'unit': 'unit', 'shelf_life': 0, **overrides})

    def test_selected_merges_preserve_quantities_money_and_original_labels(self):
        lines = []
        for a, b, _ in self.pairs:
            sale = Transaction.objects.create(handled_by=self.admin, total_amount='90.00')
            order = Order.objects.create(customer=self.customer, handled_by=self.admin, transaction=sale)
            batch = ProductBatch.objects.create(product=b, batch_number=f'ALIAS-{b.pk}',
                initial_quantity=10, remaining_quantity=7, expiration_date=timezone.localdate())
            OrderItem.objects.create(order=order, product=b, quantity=3,
                                     unit_price=30, subtotal=90, total_amount=90)
            line = TransactionItem.objects.create(transaction=sale, product_batch=batch,
                product_id_snapshot=b.pk, product_name_snapshot=b.name,
                source_product_label=f'Original worksheet: {b.name}', quantity=3,
                unit_price=30, subtotal=90, total_amount=90)
            lines.append((line, a.pk, line.source_product_label))
        excluded_before = list(Product.objects.filter(pk__in=[p.pk for p in self.excluded]).order_by('pk').values())
        quantities_before = TransactionItem.objects.aggregate(q=Sum('quantity'), amount=Sum('total_amount'))
        result = consolidate_products(self.selected)
        self.assertEqual(len(result), len(self.pairs))
        for a, b, label in self.pairs:
            a.refresh_from_db()
            self.assertEqual(a.name, label)
            self.assertFalse(Product.objects.filter(pk=b.pk).exists())
        for line, keeper, source in lines:
            line.refresh_from_db()
            self.assertEqual(line.product_id_snapshot, keeper)
            self.assertEqual(line.product_batch.product_id, keeper)
            self.assertEqual(line.source_product_label, source)
            self.assertEqual(line.quantity, Decimal('3'))
            self.assertEqual(line.unit_price, Decimal('30'))
        self.assertEqual(TransactionItem.objects.aggregate(q=Sum('quantity'), amount=Sum('total_amount')), quantities_before)
        self.assertEqual(list(Product.objects.filter(pk__in=[p.pk for p in self.excluded]).order_by('pk').values()), excluded_before)
        self.assertEqual(consolidate_products(self.selected), [])
        self.assertTrue(consolidation_plan())  # Unselected formatting duplicates remain untouched.

    def test_command_scopes_the_backup_and_audit_to_the_selected_merges(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory) / 'backups'
            original = Path
            with patch('inventory.management.commands.consolidate_products.Path',
                       side_effect=lambda value: folder if value == 'backups' else original(value)):
                call_command('consolidate_products', apply=True,
                             product_id=self.selected, stdout=StringIO())
            audit = json.loads(next(folder.glob('*/manifest.json')).read_text())
            self.assertTrue(audit['completed'])
            self.assertEqual(audit['before'], audit['after'])
            self.assertEqual(len(audit['merged']), len(self.pairs))
            self.assertTrue(Path(audit['backup']).exists())
        self.assertTrue(all(Product.objects.filter(pk=p.pk).exists() for p in self.excluded))

    def test_backup_verification_failure_rolls_back_all_selected_merges(self):
        before = list(Product.objects.order_by('pk').values())
        with TemporaryDirectory() as directory:
            folder = Path(directory) / 'backups'
            original = Path
            with patch('inventory.management.commands.consolidate_products.Path',
                       side_effect=lambda value: folder if value == 'backups' else original(value)), \
                    patch('inventory.management.commands.consolidate_products.fingerprints',
                          side_effect=[{'before': True}, {'after': True}]):
                with self.assertRaises(CommandError):
                    call_command('consolidate_products', apply=True,
                                 product_id=self.selected, stdout=StringIO())
        self.assertEqual(list(Product.objects.order_by('pk').values()), before)

    def test_aliases_with_different_units_or_variants_cannot_merge(self):
        other_unit = self.product('MELON300', unit='box')
        other_variant = self.product('Melon 300ml', variant='special')
        consolidate_products([*self.selected, other_unit.pk, other_variant.pk])
        self.assertTrue(Product.objects.filter(pk=other_unit.pk).exists())
        self.assertTrue(Product.objects.filter(pk=other_variant.pk).exists())

    def test_api_rejects_recreating_an_approved_alias(self):
        consolidate_products(self.selected)
        for alias in ['MELON200', 'MELON300', 'Mozzarella', 'Cara200',
                      'Pandan200ml', 'Pandan300ml', 'Raw300ml', 'Raw Milk300',
                      'TM200ml', 'UBE300', 'UNS300ml', 'CMTM200ML']:
            serializer = ProductSerializer(data={
                'name': alias, 'category_id': self.category.pk, 'unit': 'unit', 'shelf_life': 0,
            })
            self.assertFalse(serializer.is_valid())
            self.assertIn('name', serializer.errors)
