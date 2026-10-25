from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from forecasting.benchmarks import evaluate, predict, score
from forecasting.data import load_snapshot
from forecasting.jobs import refresh_patterns
from forecasting.models import PatternStudy
from forecasting.patterns import investigate
from forecasting.serving import report
from sales.models import Customer, Transaction, TransactionItem
from inventory.models import Category, Product, ProductBatch


class PatternStudyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user=get_user_model().objects.create_user(username='pattern-admin',email='patterns@example.test',role='admin')
        self.customer=Customer.objects.create(name='Shop',created_by=self.user)
        category=Category.objects.create(name='Pattern dairy')
        self.product=Product.objects.create(name='FML',category=category,unit='L',shelf_life=7)
        self.batch=ProductBatch.objects.create(product=self.product,batch_number='PATTERNS',
                        initial_quantity=0,remaining_quantity=0,expiration_date=date(2030,1,1),is_historical_reference=True)
        self.client=APIClient();self.client.force_authenticate(self.user)

    def sale(self,day,amount='100',**kwargs):
        sale=Transaction.objects.create(handled_by=self.user,customer=self.customer,total_amount=Decimal(amount),**kwargs)
        TransactionItem.objects.create(transaction=sale,product_batch=self.batch,quantity=1,
                                        unit_price=Decimal(amount),total_amount=Decimal(amount))
        stamp=datetime(day.year,day.month,day.day,12,tzinfo=ZoneInfo('Asia/Manila'))
        Transaction.objects.filter(pk=sale.pk).update(created_at=stamp)
        return sale

    def test_only_actual_completed_non_voided_sales_enter_the_source(self):
        self.sale(date(2024,1,1),'10',payment_status='pending')
        self.sale(date(2024,1,2),'20',source_reference='2026:OCT:123')
        self.sale(date(2024,1,3),'999',source_reference='2026:000001')
        self.sale(date(2024,1,4),'999',is_voided=True)
        self.sale(date(2024,1,5),'999')
        self.sale(date(2024,1,6),'999')
        snapshot=load_snapshot(date(2024,1,5))
        self.assertEqual(sum(s.revenue for s in snapshot.sales),30)
        self.assertEqual(snapshot.excluded_demo_count,1)
        self.assertEqual(set(snapshot.daily),{date(2024,1,1),date(2024,1,2)})

    def test_recorded_label_takes_precedence_for_feeding_classification(self):
        self.customer.name='Milk Feeding';self.customer.save()
        self.sale(date(2024,1,1),'10',source_customer_label='School canteen')
        self.sale(date(2024,1,2),'20',source_customer_label='Milk Feedin Program')
        self.sale(date(2024,1,3),'30')
        data=investigate(load_snapshot(date(2024,2,1)))
        self.assertEqual(data['regular_revenue'],'10.00')
        self.assertEqual(data['monthly'][0]['feeding_revenue'],'50.00')
        self.assertEqual(data['data_quality']['included_invoices'],3)

    def test_payment_changes_do_not_change_revenue_revision_but_voids_do(self):
        sale=self.sale(date(2024,1,1),payment_status='pending')
        revision=load_snapshot().revision
        Transaction.objects.filter(pk=sale.pk).update(payment_status='paid')
        self.assertEqual(load_snapshot().revision,revision)
        Transaction.objects.filter(pk=sale.pk).update(is_voided=True)
        self.assertNotEqual(load_snapshot().revision,revision)

    def test_unclassified_source_is_preserved_but_never_assumed_regular(self):
        sale=self.sale(date(2024,1,1),'999',source_sales_segment='unclassified')
        self.sale(date(2024,1,2),'10',source_sales_segment='regular')
        self.sale(date(2024,1,3),'20',source_sales_segment='feeding')
        findings=investigate(load_snapshot(date(2024,2,1)))
        self.assertEqual(findings['regular_revenue'],'10.00')
        self.assertEqual(findings['monthly'][0]['unclassified_revenue'],'999.00')
        self.assertEqual(findings['data_quality']['feeding_invoices'],1)
        self.assertEqual(findings['data_quality']['unclassified_invoices'],1)
        revision=findings['source_revision']
        Transaction.objects.filter(pk=sale.pk).update(source_sales_segment='feeding')
        self.assertNotEqual(load_snapshot(date(2024,2,1)).revision,revision)

    def test_new_live_sales_follow_dairy_policy_and_refresh_after_item_corrections(self):
        sweets=Product.objects.create(name='PDLX20',category=self.product.category,unit='unit',shelf_life=7)
        batch=ProductBatch.objects.create(product=sweets,batch_number='SWEETS',initial_quantity=0,
                        remaining_quantity=0,expiration_date=date(2030,1,1),is_historical_reference=True)
        mixed=self.sale(date(2024,1,1),'180')
        mixed.items.update(unit_price=100,total_amount=100)
        TransactionItem.objects.create(transaction=mixed,product_batch=batch,quantity=1,unit_price=100,total_amount=100)
        only_sweets=self.sale(date(2024,1,2),'999')
        only_sweets.items.update(product_batch=batch)
        snapshot=load_snapshot(date(2024,2,1))
        self.assertEqual(snapshot.daily,{date(2024,1,1):Decimal('90.00')})
        self.assertEqual(snapshot.excluded_non_dairy_count,1)
        revision=snapshot.revision
        mixed.items.filter(product_batch=self.batch).update(total_amount=300)
        snapshot=load_snapshot(date(2024,2,1))
        self.assertEqual(snapshot.daily[date(2024,1,1)],Decimal('135.00'))
        self.assertNotEqual(snapshot.revision,revision)

    def test_unknown_live_product_or_missing_lines_is_held_for_review(self):
        sale=self.sale(date(2024,1,1),'100')
        sale.items.update(product_name_snapshot='Unreviewed item')
        no_lines=self.sale(date(2024,1,2),'200');no_lines.items.all().delete()
        findings=investigate(load_snapshot(date(2024,2,1)))
        self.assertEqual(findings['regular_revenue'],'0.00')
        self.assertEqual(findings['data_quality']['unclassified_invoices'],2)

    def test_zero_months_are_not_invented_and_all_customers_are_audited(self):
        for index in range(13):
            customer=Customer.objects.create(name=f'Buyer {index}',created_by=self.user,is_active=index!=0)
            sale=self.sale(date(2024,1,1),'10')
            Transaction.objects.filter(pk=sale.pk).update(customer=customer)
        self.sale(date(2024,3,1),'20')
        findings=investigate(load_snapshot(date(2024,4,1)))
        self.assertEqual([row['month'] for row in findings['monthly']],['2024-01','2024-03'])
        self.assertEqual(findings['regular_customers'],14)
        self.assertEqual(sum(Decimal(row['revenue']) for row in findings['customers']),Decimal(findings['regular_revenue']))
        self.assertFalse(findings['coverage_verified'])
        self.assertFalse(findings['eligible_for_publication'])

    def test_cadence_uses_last_observed_day_instead_of_unknown_future_months(self):
        for day in (1,8,15,22):self.sale(date(2024,1,day))
        findings=investigate(load_snapshot(date(2025,1,1)))
        customer=findings['customers'][0]
        self.assertEqual(customer['median_gap_days'],7)
        self.assertEqual(customer['gap_cv'],0)
        self.assertEqual(customer['days_since_last_purchase'],0)
        self.assertEqual(customer['activity_reference_date'],'2024-01-22')
        self.assertFalse(customer['possible_historical_lapse'])
        self.assertGreater(findings['data_quality']['days_since_latest_actual'],300)

    def test_source_revision_and_cutoff_make_refresh_incremental_and_retain_history(self):
        self.sale(date(2024,1,1))
        first,created=refresh_patterns(date(2024,2,1));self.assertTrue(created)
        repeated,created=refresh_patterns(date(2024,2,1));self.assertFalse(created)
        self.assertEqual(first.pk,repeated.pk)
        self.sale(date(2024,1,2))
        second,created=refresh_patterns(date(2024,2,1));self.assertTrue(created)
        self.assertNotEqual(first.source_revision,second.source_revision)
        self.assertEqual(PatternStudy.objects.count(),2)
        first.refresh_from_db()
        self.assertEqual(first.findings['data_quality']['included_invoices'],1)
        self.assertEqual(second.findings['data_quality']['included_invoices'],2)

    def test_ledger_race_does_not_save_an_inconsistent_study(self):
        self.sale(date(2024,1,1))
        original=load_snapshot(date(2024,2,1))
        self.sale(date(2024,1,2))
        changed=load_snapshot(date(2024,2,1))
        with patch('forecasting.jobs.load_snapshot',side_effect=[original,changed]):
            with self.assertRaisesMessage(ValueError,'Sales changed'):
                refresh_patterns(date(2024,2,1))
        self.assertEqual(PatternStudy.objects.count(),0)

    def test_forecast_endpoint_withholds_predictions_and_reports_research_target(self):
        self.sale(date(2024,1,1))
        response=self.client.get('/api/forecasting/forecast/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['forecast'],[])
        self.assertEqual(response.data['status'],'pending_update')
        self.assertEqual(response.data['accuracy_target_percent'],15)
        self.assertEqual(response.data['metrics'],{})
        self.assertEqual(response.data['stage'],'pattern_investigation')
        self.assertEqual(self.client.get('/api/forecasting/forecast/?period=invalid').status_code,400)

    def test_patterns_endpoint_needs_offline_refresh_and_detects_new_ledger_data(self):
        self.sale(date(2024,1,1))
        self.assertEqual(self.client.get('/api/forecasting/patterns/').data['status'],'refresh_required')
        with self.captureOnCommitCallbacks(execute=True):refresh_patterns()
        data=self.client.get('/api/forecasting/patterns/').data
        self.assertEqual(data['status'],'current')
        self.assertEqual(data['findings']['data_quality']['included_invoices'],1)
        with self.captureOnCommitCallbacks(execute=True):self.sale(date(2024,1,2))
        self.assertEqual(self.client.get('/api/forecasting/patterns/').data['status'],'refresh_required')

    def test_staff_and_anonymous_cannot_access_global_research(self):
        staff=get_user_model().objects.create_user(username='pattern-staff',email='staff-patterns@example.test',role='staff')
        for user,expected in ((staff,403),(None,401)):
            self.client.force_authenticate(user)
            for endpoint in ('patterns','forecast'):
                self.assertEqual(self.client.get(f'/api/forecasting/{endpoint}/').status_code,expected)

    def test_commands_are_repeatable_and_do_not_publish_forecasts(self):
        self.sale(date(2024,1,1))
        output=StringIO()
        call_command('refresh_forecast_patterns',stdout=output)
        call_command('refresh_forecast_patterns',stdout=output)
        self.assertEqual(PatternStudy.objects.count(),1)
        self.assertEqual(report()['forecast'],[])
        with self.assertRaises(CommandError):
            call_command('refresh_forecast_patterns',as_of=timezone.localdate()+timedelta(days=1),stdout=output)


class ResearchBenchmarkTests(SimpleTestCase):
    def months(self,count=36):
        return [{'month':f'{2020+i//12:04}-{i%12+1:02}', 'regular_revenue':str(100+i)} for i in range(count)]

    def test_research_uses_chronological_windows_and_not_test_selected_winners(self):
        months=self.months()
        result=evaluate(months)
        self.assertEqual(result['test_start'],'2022-01')
        self.assertEqual(result['validation_start'],'2021-07')
        expected=min(result['benchmarks'],key=lambda name:(result['benchmarks'][name]['validation']['wape_percent'],name))
        self.assertEqual(result['selected_benchmark'],expected)
        for candidate in result['benchmarks'].values():
            for row in candidate['validation_rows']+candidate['test_rows']:
                self.assertLess(row['trained_through'],row['month'])
        for row in months[-12:]:row['regular_revenue']='100000'
        self.assertEqual(evaluate(months)['selected_benchmark'],result['selected_benchmark'])

    def test_new_month_advances_windows_without_hardcoded_test_year(self):
        before=evaluate(self.months(36));after=evaluate(self.months(37))
        self.assertEqual(before['test_start'],'2022-01')
        self.assertEqual(after['test_start'],'2022-02')
        self.assertEqual(after['test_end'],'2023-01')

    def test_gap_does_not_become_a_zero_month_for_backtesting(self):
        rows=self.months();del rows[20]
        result=evaluate(rows)
        self.assertEqual(result['status'],'insufficient_history')
        self.assertEqual(result['available_contiguous_months'],15)

    def test_zero_actuals_have_no_fabricated_accuracy(self):
        self.assertIsNone(score([{'actual':0,'predicted':1}])['wape_percent'])
        rows=self.months()
        for row in rows:row['regular_revenue']='0'
        self.assertIsNone(evaluate(rows)['selected_benchmark'])
        self.assertIsNone(evaluate(rows)['selected_test'])

    def test_each_benchmark_uses_only_its_available_history(self):
        self.assertEqual(predict(list(range(1,13)),'same_month_last_year'),1)
        self.assertEqual(predict([1,2,3,4],'trailing_3_mean'),3)
        self.assertEqual(predict([1,100,3],'trailing_3_median'),3)
