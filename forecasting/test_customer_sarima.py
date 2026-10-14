from datetime import date, datetime, time
from unittest.mock import patch

import numpy as np
import pandas as pd
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from sales.models import Customer, Transaction
from .customer_sarima import evaluate_period, read_source, select_customers, specifications
from .data import source
from .segmented_sarima import Model


class CustomerSarimaTests(SimpleTestCase):
    def history(self):
        dates = pd.date_range('2023-01-01', '2025-12-31')
        steady = pd.Series(np.where(dates.dayofweek == 1, 1000., 0.), index=dates)
        intermittent = pd.Series(np.where((dates < '2024-06-01') & (dates.day.isin([1, 15])), 500., 0.), index=dates)
        new = pd.Series(np.where(dates >= '2025-07-01', 200., 0.), index=dates)
        unassigned = pd.Series(10., index=dates)
        feeding = pd.Series(np.where((dates >= '2024-11-01') & (dates.day == 15), 10000., 0.), index=dates)
        regular = steady + intermittent + new + unassigned
        customers = {key: {'name': key, 'daily': values} for key, values in
                     [('steady', steady), ('intermittent', intermittent), ('new', new), ('unassigned', unassigned)]}
        return regular + feeding, {'feeding': feeding, 'other_sales': regular}, customers

    @staticmethod
    def model():
        return Model('daily', 91, 'level', (1, 0, 0), (1, 0, 0, 7))

    @staticmethod
    def fake_fit(history, spec, steps):
        return np.repeat(history.iloc[-(spec.window or len(history)):].mean(), steps)

    def test_membership_ignores_future_sales_and_does_not_infer_relationship_termination(self):
        _, _, customers = self.history()
        first, audit = select_customers(customers)
        self.assertIn('intermittent', first)
        self.assertNotIn('new', first)
        self.assertNotIn('unassigned', first)
        for customer in customers.values():
            customer['daily'].loc['2025-01-01':] *= 100000
        second, changed_audit = select_customers(customers)
        self.assertEqual(first, second)
        self.assertEqual(audit, changed_audit)

    def test_grid_requires_customer_annual_history_and_uses_active_seasonal_terms(self):
        early = specifications('monthly', pd.Period('2023-01', freq='M'))
        late = specifications('monthly', pd.Period('2024-03', freq='M'))
        self.assertTrue(any(s.frequency == 'monthly' for s in early))
        self.assertTrue(all(s.frequency == 'daily' for s in late))
        self.assertTrue(all(s.seasonal_order[-1] == 7 for s in specifications('weekly', pd.Period('2023-01', freq='M'))))
        self.assertTrue(all(any(s.seasonal_order[:3]) for s in early))

    def test_all_sales_reconcile_and_both_approaches_share_identical_feeding_forecasts(self):
        daily, parts, customers = self.history()
        membership, _ = select_customers(customers)
        with patch('forecasting.customer_sarima.specifications', return_value=(self.model(),)), \
             patch('forecasting.tuned_sarima.fit', side_effect=self.fake_fit):
            result = evaluate_period(daily, parts, customers, membership, 'monthly', self.model())
        a, b = result['approaches']['aggregate_regular'], result['approaches']['customer_regular']
        self.assertEqual(a['metrics']['feeding'], b['metrics']['feeding'])
        self.assertEqual(len(a['rows']), 6)
        for index, (left, right) in enumerate(zip(a['rows'], b['rows'])):
            self.assertEqual(left['actual'], right['actual'])
            self.assertAlmostEqual(left['predicted']-a['regular_rows'][index]['predicted'],
                                   right['predicted']-b['regular_rows'][index]['predicted'], places=2)
            self.assertAlmostEqual(b['regular_rows'][index]['actual'],
                sum(rows[index]['actual'] for rows in result['customer_rows'].values()), places=2)
            self.assertLess(left['trained_through'], left['date'])
        self.assertIn('pooled_remaining', result['customer_metrics'])

    def test_holdout_cannot_change_customer_models_structure_or_ranges(self):
        daily, parts, customers = self.history()
        membership, _ = select_customers(customers)
        with patch('forecasting.customer_sarima.specifications', return_value=(self.model(),)), \
             patch('forecasting.tuned_sarima.fit', side_effect=self.fake_fit):
            first = evaluate_period(daily, parts, customers, membership, 'monthly', self.model())
            daily.loc['2025-07-01':] *= 10
            for values in parts.values():
                values.loc['2025-07-01':] *= 10
            for customer in customers.values():
                customer['daily'].loc['2025-07-01':] *= 10
            second = evaluate_period(daily, parts, customers, membership, 'monthly', self.model())
        self.assertEqual(first['selection'], second['selection'])
        self.assertEqual(first['selection_signature'], second['selection_signature'])
        self.assertNotEqual(first['approaches']['customer_regular']['metrics'], second['approaches']['customer_regular']['metrics'])

    def test_crossing_week_does_not_select_models_and_all_test_cutoffs_are_past(self):
        daily, parts, customers = self.history()
        membership, _ = select_customers(customers)
        with patch('forecasting.customer_sarima.specifications', return_value=(self.model(),)), \
             patch('forecasting.tuned_sarima.fit', side_effect=self.fake_fit):
            result = evaluate_period(daily, parts, customers, membership, 'weekly', self.model())
        self.assertEqual(result['selection']['validation_dates'][-1], '2025-06-23')
        for approach in result['approaches'].values():
            self.assertEqual(approach['rows'][0]['date'], '2025-07-07')
            self.assertEqual(approach['metrics']['combined']['rows'], 25)
            self.assertTrue(all(r['trained_through'] < r['date'] for r in approach['rows']))

    def test_unreconciled_customer_partition_is_rejected(self):
        daily, parts, customers = self.history()
        customers.pop('unassigned')
        with self.assertRaisesRegex(ValueError, 'reconcile'):
            evaluate_period(daily, parts, customers, ['steady'], 'monthly', self.model())


class CustomerSourceTests(TestCase):
    def test_customer_aliases_are_linked_unassigned_sales_remain_and_assignments_change_signature(self):
        user = get_user_model().objects.create_user(username='reader', email='reader@example.test')
        a = Customer.objects.create(name='Established A', created_by=user)
        b = Customer.objects.create(name='Established B', created_by=user)
        sales = []
        for customer, label, amount in [(a, 'A alias', 100), (a, 'Another alias', 200),
                                         (None, '', 50), (b, 'Milk Feeding', 1000)]:
            sale = Transaction.objects.create(handled_by=user, customer=customer,
                                              source_customer_label=label, total_amount=amount)
            sales.append(sale)
        Transaction.objects.filter(pk__in=[s.pk for s in sales]).update(
            created_at=timezone.make_aware(datetime.combine(date(2025, 12, 31), time(12))))
        original_real = source()[1]
        daily, parts, customers, signature, real_signature = read_source()
        self.assertEqual(real_signature, original_real)
        self.assertEqual(customers[str(a.pk)]['daily'].sum(), 300)
        self.assertEqual(customers['unassigned']['daily'].sum(), 50)
        self.assertNotIn(str(b.pk), customers)
        self.assertEqual(parts['feeding'].sum(), 1000)
        self.assertEqual(daily.sum(), 1350)
        Transaction.objects.filter(pk=sales[0].pk).update(customer=b)
        self.assertEqual(source()[1], original_real)
        self.assertNotEqual(read_source()[3], signature)
