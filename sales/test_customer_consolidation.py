from copy import deepcopy
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, TestCase
from config.test_support import APITestCase
from sales.importers.annual_sales import canonical_customer, customer_key
from sales.models import Customer, Transaction, Order
from sales.services.customer_consolidation import consolidation_plan, consolidate_customers


class CustomerNamePolicyTests(SimpleTestCase):
    def test_known_event_annotations_resolve_to_one_identity(self):
        for value in ['Ala Eh Batangas Dec.8', 'Ala Eh Dec 11 Sales', 'Ala Eh Festival Dec.10', 'Ala Eh December 19 Sales']:
            self.assertEqual(canonical_customer(value), 'Ala Eh Festival')
        for value, expected in [('Dairy Box Aug.24', 'Dairy Box Sales'), ('Dairy Box Aug 25', 'Dairy Box Sales'),
                ('Tikme-Day 3', 'Tikme'), ('Tikme Aug 14-16', 'Tikme'), ('BSU Day 3', 'BSU'),
                ('SM Day 5', 'SM'), ('San Jose Day2', 'San Jose'), ('Sta. Cruz Day 1', 'Sta. Cruz'),
                ('Kadiwa-DBM Dec 15-16', 'Kadiwa-DBM'), ('Tri Star Batangas Corp.', 'Tri Star')]:
            self.assertEqual(canonical_customer(value), expected)
            self.assertEqual(canonical_customer(expected), expected)

    def test_distinct_branches_programs_and_unconfirmed_people_remain_distinct(self):
        for a,b in [('Kadiwa-BIR','BIR'), ('Kadiwa-Sta. Rosa','Sta. Rosa'), ('BSU','BSU-MAIN'),
                    ('Milk Feeding-DSWD','Milk Feeding-DEPED'), ('Milk Feeding week1','Milk Feeding WEEK 3'),
                    ('Cassie Alagar','Catherine S.'), ('Sir Sonny','Sir Anton'),
                    ('WTC_A','WTC-Alabang'), ('SM','SMX'), ('Camsur','Camsur MPC')]:
            self.assertNotEqual(customer_key(a), customer_key(b))

    def test_owner_confirmed_personal_aliases_use_requested_canonical_names(self):
        groups = {'Jamie Ortiz': ['Jaime Ortiz', 'Jamie Ortiz'],
                  'Jonny Decena': ['Jhonny Decena', 'Jonny Decena'],
                  'Cassie Alagar': ['Cassie', 'Cassie A.', 'Cassie Alagar'],
                  'Sir Sonny': ['Sonny', 'Sir Sonny', 'Sonny Bombay']}
        for name, aliases in groups.items():
            for alias in aliases:
                self.assertEqual(canonical_customer(alias), name)
        self.assertNotEqual(customer_key('Jonny Decena'), customer_key('Joseph Caguimbal'))


class CustomerConsolidationTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='customer-audit',email='audit@example.test')

    def customer(self, name, **kwargs):
        return Customer.objects.create(name=name, created_by=self.owner, **kwargs)

    def test_merges_links_preserves_every_other_receipt_field_and_is_idempotent(self):
        keeper = self.customer('Ala Eh Festival', is_active=False)
        alias = self.customer('Ala Eh Dec 11 Sales', email='event@example.test')
        sale = Transaction.objects.create(handled_by=self.owner, customer=alias, subtotal=123,
            total_amount=123, source_invoice_number='SI-ORIGINAL', source_customer_label=alias.name,
            source_reference='2025:DECEMBER:123', source_sales_segment='regular', payment_status='pending')
        order = Order.objects.create(handled_by=self.owner, customer=alias, transaction=sale)
        old_sale = Transaction.objects.values().get(pk=sale.pk)
        old_order = Order.objects.values().get(pk=order.pk)
        plan = consolidate_customers()
        sale.refresh_from_db(); order.refresh_from_db(); keeper.refresh_from_db()
        self.assertEqual((sale.customer_id, order.customer_id), (keeper.pk,keeper.pk))
        self.assertEqual(keeper.email,'event@example.test')
        self.assertTrue(keeper.is_active)
        self.assertFalse(Customer.objects.filter(pk=alias.pk).exists())
        expected = {**old_sale,'customer_id':keeper.pk}
        self.assertEqual(Transaction.objects.values().get(pk=sale.pk),expected)
        self.assertEqual(Order.objects.values().get(pk=order.pk),{**old_order,'customer_id':keeper.pk})
        self.assertEqual(plan['merge_groups'][0]['old_transaction_links'],[{'id':sale.pk,'customer_id':alias.pk}])
        self.assertEqual(consolidation_plan()['merge_groups'],[])

    def test_contact_conflicts_and_unknown_same_names_require_review(self):
        self.customer('Dairy Box',email='one@example.test')
        self.customer('Dairy Box Sales',email='two@example.test')
        self.customer('Unconfirmed Person');self.customer('Unconfirmed Person')
        self.assertEqual(consolidation_plan()['merge_groups'],[])
        self.assertEqual(len(consolidation_plan()['needs_review']),2)
        consolidate_customers()
        self.assertEqual(Customer.objects.count(),4)

    def test_command_previews_then_backs_up_and_verifies_apply(self):
        self.customer('Tikme');self.customer('Tikme-Day 2')
        stream=StringIO();call_command('consolidate_customers',stdout=stream)
        self.assertEqual(len(json.loads(stream.getvalue())['merge_groups']),1)
        self.assertEqual(Customer.objects.count(),2)
        with TemporaryDirectory() as folder:
            root=Path(folder)/'backups'
            original=Path
            def paths(value):
                return root if value=='backups' else original(value)
            with patch('sales.management.commands.consolidate_customers.Path',side_effect=paths):
                call_command('consolidate_customers',apply=True,stdout=StringIO())
            audit=json.loads(next(root.glob('*/manifest.json')).read_text())
            self.assertTrue(audit['completed'])
            self.assertEqual(audit['before'],audit['after'])
            self.assertTrue(Path(audit['backup']).exists())
        self.assertEqual(Customer.objects.count(),1)

    def test_invariant_failure_rolls_back_all_customer_changes(self):
        a=self.customer('Tikme');b=self.customer('Tikme-Day 2')
        with TemporaryDirectory() as folder:
            root=Path(folder)/'backups'
            original=Path
            with patch('sales.management.commands.consolidate_customers.Path',
                       side_effect=lambda value:root if value=='backups' else original(value)), \
                    patch('sales.management.commands.consolidate_customers.fingerprints',side_effect=[{'unchanged':1},{'unexpected':2}]):
                with self.assertRaises(CommandError):
                    call_command('consolidate_customers',apply=True,stdout=StringIO())
        self.assertEqual(set(Customer.objects.values_list('pk',flat=True)),{a.pk,b.pk})


class CustomerAliasAPITests(APITestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user(username='customer-admin',email='customer-admin@example.test',role='admin')
        self.client.force_authenticate(self.owner)

    def test_api_canonicalizes_events_and_rejects_recreating_alias(self):
        first=self.client.post('/sales/customers/',{'name':'Ala Eh Dec 15 Sales'},format='json')
        self.assertEqual(first.status_code,201)
        self.assertEqual(first.data['name'],'Ala Eh Festival')
        second=self.client.post('/sales/customers/',{'name':'Ala Eh Batangas Dec.8'},format='json')
        self.assertEqual(second.status_code,400)
        self.assertEqual(Customer.objects.count(),1)

    def test_same_named_people_with_conflicting_contacts_can_stay_separate(self):
        Customer.objects.create(name='Same Person',email='one@example.test',created_by=self.owner)
        response=self.client.post('/sales/customers/',{'name':'Same Person','email':'two@example.test'},format='json')
        self.assertEqual(response.status_code,201)
        self.assertEqual(Customer.objects.count(),2)
