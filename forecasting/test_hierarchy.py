from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from django.utils import timezone
from config.test_support import APITestCase
from inventory.models import Category, Product, ProductBatch
from sales.models import Transaction, TransactionItem
from .hierarchy import build, cutoff_for, covered_cutoff, fit_series, predict, refresh, report, run_revision, VERSION
from .hierarchy_source import load_source
from .models import HierarchyForecastRun
from .provisional import digest


def source_data():
    days = [date(2022, 1, 3)+timedelta(weeks=i) for i in range(200)]
    products = {1: {'id': 1, 'name': 'FML', 'variant': '', 'unit': 'unit', 'category_id': 1,
        'category_name': 'Milk', 'is_active': True, 'shelf_life': 7},
        2: {'id': 2, 'name': 'Bulk', 'variant': '', 'unit': 'L', 'category_id': 1,
        'category_name': 'Milk', 'is_active': True, 'shelf_life': 7}}
    facts = [{'day': day, 'segment': segment, 'product_id': pid,
              'quantity': Decimal('10'), 'revenue': Decimal('80')}
             for day in days for segment in ('regular', 'feeding') for pid in products]
    return {'facts': facts, 'products': products, 'coverage_days': set(days),
            'latest_day': days[-1]+timedelta(days=6), 'earliest_day': days[0],
            'revision': 'fixture', 'audit': {}}


def repeat_last_sarima(values, method, horizon, season):
    last = next(float(value) for value in reversed(values) if value is not None)
    return [last]*horizon


class HierarchyMathTests(SimpleTestCase):
    def test_non_sarima_legacy_methods_are_disabled(self):
        values = [1., 2., 3., 4., 5., 6., 7., 11., None, 13., 14., 15., 16., 17.]
        with self.assertRaises(ValueError):
            predict(values, 'seasonal_naive', 7, 7)

    def test_long_projection_advances_seasonality_and_withholds_unvalidated_ranges(self):
        days = [date(2024, 1, 1)+timedelta(weeks=i) for i in range(116)]
        targets = [days[-1]+timedelta(weeks=i+1) for i in range(60)]
        values = list(range(1, 53))*2 + list(range(1, 13))
        with patch('forecasting.hierarchy.predict', side_effect=repeat_last_sarima):
            result = fit_series(values, days, targets, 'weekly')
        self.assertEqual(result['method'], 'sarima')
        self.assertEqual(set(result['candidates']), {'sarima'})
        self.assertEqual(len(result['forecast']), 60)
        self.assertIsNotNone(result['forecast'][0]['value'])
        self.assertEqual(result['forecast'][44]['steps_ahead'], 45)
        self.assertIsNotNone(result['forecast'][0]['lower'])
        self.assertIsNone(result['forecast'][44]['lower'])
        self.assertIsNone(result['forecast'][44]['upper'])
        self.assertEqual(result['metrics']['periods'], 8)

    def test_current_projection_preserves_original_forecasts_and_training_history(self):
        source = source_data()
        with patch('forecasting.hierarchy.predict', side_effect=repeat_last_sarima):
            original = build(source, 'weekly', as_of=date(2025, 11, 10))
            current = build(source, 'weekly', as_of=date(2026, 11, 5))
        self.assertEqual(current['projection_start'], '2026-11-02')
        self.assertEqual(current['trained_through'], original['trained_through'])
        self.assertEqual(current['source_revision'], source['revision'])
        old = original['views']['regular']['items'][0]['demand']
        new = current['views']['regular']['items'][0]['demand']
        current_by_date = {point['date']: point for point in new['forecast']}
        self.assertTrue(all(current_by_date.get(point['date']) == point for point in old['forecast']
                            if point['date'] in current_by_date))
        self.assertEqual(new['history'], old['history'])
        self.assertEqual(new['metrics'], old['metrics'])
        self.assertEqual(new['method'], old['method'])
        projected = [p for p in new['forecast'] if p['date'] >= current['projection_start']]
        self.assertEqual(len(projected), 8)
        self.assertTrue(all(p['value'] is not None and p['value'] >= 0 for p in projected))
        self.assertEqual(new['method'], 'sarima')
        self.assertEqual(projected[-1]['date'], '2026-12-21')
        self.assertLessEqual(len(new['forecast']), 8+3+8)

    def test_saved_projection_revision_follows_the_business_period(self):
        source = source_data()
        for period, same, next_period in [
            ('daily', date(2026, 11, 5), date(2026, 11, 6)),
            ('weekly', date(2026, 11, 8), date(2026, 11, 9)),
            ('monthly', date(2026, 11, 30), date(2026, 12, 1)),
        ]:
            initial = run_revision(source, period, date(2026, 11, 5))
            self.assertEqual(initial, run_revision(source, period, same))
            self.assertNotEqual(initial, run_revision(source, period, next_period))

    def test_cutoffs_never_create_unrecorded_tail_periods(self):
        self.assertEqual(cutoff_for(date(2025, 12, 31), 'daily'), date(2026, 1, 1))
        self.assertEqual(cutoff_for(date(2025, 12, 31), 'weekly'), date(2025, 12, 29))
        self.assertEqual(cutoff_for(date(2025, 12, 31), 'monthly'), date(2026, 1, 1))
        self.assertEqual(cutoff_for(date(2025, 12, 20), 'monthly'), date(2025, 12, 1))

    def test_sales_in_an_incomplete_week_do_not_erase_the_covered_training_run(self):
        source=source_data()
        with patch('forecasting.hierarchy.predict', side_effect=repeat_last_sarima):
            original=build(source,'weekly',as_of=date(2026,11,5))
        recent=date(2026,11,4)
        source['coverage_days'].add(recent)
        source['facts'].append({**source['facts'][0],'day':recent,'quantity':Decimal('99')})
        source['latest_day']=recent
        with patch('forecasting.hierarchy.predict', side_effect=repeat_last_sarima):
            current=build(source,'weekly',as_of=date(2026,11,5))
        self.assertEqual(current['trained_through'],original['trained_through'])
        self.assertEqual(current['views']['regular']['items'][0]['demand'],original['views']['regular']['items'][0]['demand'])
        self.assertEqual(current['projection_start'],'2026-11-02')
        self.assertEqual(covered_cutoff(source,'daily'),date(2026,11,5))
        self.assertEqual(covered_cutoff(source,'monthly'),date(2025,11,1))

    def test_native_units_and_all_hierarchy_levels_reconcile(self):
        with patch('forecasting.hierarchy.predict', side_effect=repeat_last_sarima):
            result = build(source_data(), 'weekly')
        self.assertEqual(set(result['views']['combined']['overall']['demand']), {'L', 'unit'})
        for index in range(8):
            combined = result['views']['combined']
            def total(rows):
                values = [row['value'] for row in rows if row['value'] is not None]
                return round(sum(values), 2) if values else None
            expected = total([v['overall']['revenue']['forecast'][index]
                for key, v in result['views'].items() if key != 'combined'])
            self.assertEqual(combined['overall']['revenue']['forecast'][index]['value'], expected)
            self.assertEqual(expected, total([i['revenue']['forecast'][index] for i in combined['items']]))
            self.assertEqual(expected, total([c['revenue']['forecast'][index] for c in combined['categories']]))

    def test_sparse_movement_stays_unavailable_and_does_not_inflate_totals(self):
        source = source_data()
        source['products'][3] = {**source['products'][1], 'id': 3, 'name': 'MELON200'}
        source['facts'].append({'day': source['earliest_day'], 'segment': 'regular', 'product_id': 3,
                                'quantity': Decimal('1'), 'revenue': Decimal('30')})
        with patch('forecasting.hierarchy.predict', side_effect=repeat_last_sarima):
            result = build(source, 'weekly')['views']['combined']
        sparse = next(i for i in result['items'] if i['id'] == 3)
        for field in ('demand', 'revenue'):
            self.assertEqual(sparse[field]['status'], 'insufficient_history')
            self.assertTrue(all(p['value'] is None for p in sparse[field]['forecast']))
            self.assertIsNone(sparse[field]['metrics'])
        total = result['overall']['demand']['unit']
        self.assertEqual(total['status'], 'partial')
        self.assertEqual(total['supported_series'], 2)
        self.assertEqual(total['total_series'], 3)
        self.assertTrue(all(p['value'] == 20 for p in total['forecast']))
        self.assertTrue(all(p['value'] == 320 for p in result['overall']['revenue']['forecast']))

    def test_recent_sparse_movement_preserves_observations_without_filling_forecasts(self):
        days = [date(2025, 12, 1)+timedelta(days=i) for i in range(4)]
        targets = [date(2025, 12, 5)+timedelta(days=i) for i in range(14)]
        result = fit_series([6., None, 0., 3.], days, targets, 'daily')
        self.assertEqual(result['status'], 'insufficient_history')
        self.assertTrue(all(p['value'] is None and p['lower'] is None and p['upper'] is None
                            for p in result['forecast']))
        self.assertIsNone(result['metrics'])
        self.assertEqual(result['test'], [])
        self.assertEqual([p['actual'] for p in result['history']], [6., None, 0., 3.])
        self.assertEqual(result['supported_series'], 0)

    def test_model_fit_failure_does_not_fall_back_to_a_movement_rate(self):
        days = [date(2023, 1, 2)+timedelta(weeks=i) for i in range(116)]
        targets = [days[-1]+timedelta(weeks=i+1) for i in range(8)]
        with patch('forecasting.hierarchy.predict', side_effect=ValueError('Final fit failed')):
            result = fit_series([10.]*116, days, targets, 'weekly')
        self.assertEqual(result['status'], 'fit_failed')
        self.assertTrue(all(p['value'] is None for p in result['forecast']))
        self.assertIsNone(result['metrics'])

    def test_entirely_unsupported_hierarchy_remains_unavailable(self):
        source = source_data()
        source['facts'] = source['facts'][:1]
        result = build(source, 'weekly')['views']['combined']['overall']
        for series in (result['demand']['unit'], result['revenue']):
            self.assertEqual(series['status'], 'insufficient_history')
            self.assertEqual(series['supported_series'], 0)
            self.assertTrue(all(p['value'] is None for p in series['forecast']))

    def test_no_movement_and_inactive_items_cannot_receive_invented_estimates(self):
        days = [date(2025, 12, 1)]
        targets = [date(2026, 1, 1)]
        for values, active in [([None], True), ([0.], True), ([10.], False)]:
            result = fit_series(values, days, targets, 'daily', active=active)
            self.assertIsNone(result['forecast'][0]['value'])

    def test_later_test_values_never_select_the_model(self):
        dates = [date(2023, 1, 2)+timedelta(weeks=i) for i in range(116)]
        targets = [dates[-1]+timedelta(weeks=i+1) for i in range(8)]
        with patch('forecasting.hierarchy.predict', side_effect=repeat_last_sarima):
            original = fit_series([10.0]*116, dates, targets, 'weekly')
            changed = fit_series([10.0]*108+[1000.0]*8, dates, targets, 'weekly')
        self.assertEqual(original['method'], changed['method'])
        self.assertNotEqual(original['metrics'], changed['metrics'])

    def test_sarima_candidate_eligibility_uses_only_earlier_observations(self):
        from .learning import shift_month
        dates = [shift_month(date(2023, 1, 1), n) for n in range(36)]
        targets = [shift_month(date(2026, 1, 1), n) for n in range(12)]
        earlier = [0.0]*8+[10.0]*20
        with patch('forecasting.hierarchy.predict', side_effect=repeat_last_sarima):
            original = fit_series(earlier+[0.0]*8, dates, targets, 'monthly')
            changed = fit_series(earlier+[1000.0]*8, dates, targets, 'monthly')
        self.assertEqual(set(original['candidates']), {'sarima'})
        self.assertEqual(set(changed['candidates']), {'sarima'})



class HierarchySourceTests(APITestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username='hierarchy', email='hierarchy@example.test', role='admin')
        self.staff = get_user_model().objects.create_user(username='hierarchy-staff', email='hierarchy-staff@example.test', role='staff')
        category = Category.objects.create(name='Milk')
        product = Product.objects.create(name='FML', category=category, unit='unit', shelf_life=7)
        self.batch = ProductBatch.objects.create(product=product, batch_number='HIERARCHY',
            initial_quantity=100, remaining_quantity=100, expiration_date=date(2027, 1, 1))
        self.client.force_authenticate(self.admin)

    def sale(self, amount='90', segment='regular', **kwargs):
        sale = Transaction.objects.create(handled_by=self.admin, total_amount=amount,
                                          source_sales_segment=segment, **kwargs)
        Transaction.objects.filter(pk=sale.pk).update(created_at=timezone.make_aware(datetime(2025, 12, 10, 12)))
        return sale

    def line(self, sale, amount='100', quantity=1):
        return TransactionItem.objects.create(transaction=sale, product_batch=self.batch,
            quantity=quantity, unit_price=0, source_line_total=amount,
            product_id_snapshot=self.batch.product_id, product_name_snapshot='FML')

    def test_invoice_discounts_are_allocated_once_to_the_exact_net_total(self):
        sale = self.sale()
        self.line(sale, '100'); self.line(sale, '50')
        facts = load_source()['facts']
        self.assertEqual([f['revenue'] for f in facts], [Decimal('60'), Decimal('30')])
        self.assertEqual(sum(f['quantity'] for f in facts), 2)

    def test_many_small_lines_never_get_negative_rounding_remainders(self):
        sale = self.sale('0.02')
        for _ in range(10): self.line(sale, '1')
        values = [f['revenue'] for f in load_source()['facts']]
        self.assertEqual(sum(values), Decimal('0.02'))
        self.assertTrue(all(v >= 0 for v in values))

    def test_unpriced_invoice_keeps_quantity_and_unallocated_revenue(self):
        sale = self.sale()
        self.line(sale, None)
        source = load_source()
        self.assertEqual(source['facts'][0]['quantity'], 1)
        self.assertIsNone(source['facts'][0]['revenue'])
        self.assertEqual(source['facts'][1]['product_id'], 0)
        self.assertEqual(source['facts'][1]['revenue'], 90)

    def test_void_demo_and_unclassified_records_do_not_enter_training(self):
        for kwargs in [{'is_voided': True}, {'source_reference': '2026:000001'}, {'segment': 'unclassified'}]:
            self.line(self.sale(**kwargs))
        self.assertEqual(load_source()['facts'], [])

    def test_get_requires_admin_and_never_trains_or_creates_a_run(self):
        with patch('forecasting.hierarchy.build', side_effect=AssertionError('GET must not train')):
            self.assertEqual(self.client.get('/api/forecasting/hierarchy/').status_code, 200)
        self.assertEqual(HierarchyForecastRun.objects.count(), 0)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get('/api/forecasting/hierarchy/').status_code, 403)

    def test_saved_run_checksums_and_source_changes_withhold_old_results(self):
        source = source_data()
        with patch('forecasting.hierarchy.load_source', return_value=source):
            legacy = HierarchyForecastRun.objects.create(engine_version='item-hierarchy-v3',
                period='weekly', source_revision=source['revision'], cutoff=date(2025, 10, 13),
                findings={}, checksum=digest({}))
            self.assertEqual(report('weekly')['status'], 'refresh_required')
            run, _ = refresh('weekly')
            self.assertEqual(run.engine_version, VERSION)
            self.assertNotEqual(run.pk, legacy.pk)
            self.assertTrue(HierarchyForecastRun.objects.filter(pk=legacy.pk).exists())
            self.assertEqual(report('weekly')['status'], 'ready')
            HierarchyForecastRun.objects.filter(pk=run.pk).update(checksum='bad')
            self.assertEqual(report('weekly')['status'], 'refresh_required')
            source['revision'] = 'changed'
            self.assertEqual(report('weekly')['status'], 'refresh_required')

    def test_period_rollover_requires_a_new_saved_projection_and_get_never_fits(self):
        source = source_data()
        with patch('forecasting.hierarchy.load_source', return_value=source):
            with patch('forecasting.hierarchy.timezone.localdate', return_value=date(2026, 11, 5)):
                first, created = refresh('weekly')
                self.assertTrue(created)
                repeated, created = refresh('weekly', as_of=date(2026, 11, 8))
                self.assertFalse(created)
                self.assertEqual(repeated.pk, first.pk)
                self.assertEqual(report('weekly')['status'], 'ready')
            with patch('forecasting.hierarchy.timezone.localdate', return_value=date(2026, 11, 9)):
                with patch('forecasting.hierarchy.build', side_effect=AssertionError('GET must not fit')):
                    self.assertEqual(report('weekly')['status'], 'refresh_required')
                second, created = refresh('weekly')
                self.assertTrue(created)
                self.assertNotEqual(second.pk, first.pk)
                self.assertEqual(report('weekly')['status'], 'ready')
