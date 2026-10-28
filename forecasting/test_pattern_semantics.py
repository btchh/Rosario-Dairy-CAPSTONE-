"""Invoice-only monthly diagnostics preserve missingness and completed periods."""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from .data import RecordedSale, SalesSnapshot
from .patterns import ANALYZER_VERSION, investigate


class PatternMonthSemanticsTests(SimpleTestCase):
    def snapshot(self, count=42, replacements=None, omitted=(), as_of=None):
        replacements = replacements or {}
        sales = []
        for index in range(count):
            if index in omitted:
                continue
            segment, amount = replacements.get(index, ('regular', 100 + index))
            sales.append(RecordedSale(
                index + 1, date(2023 + index // 12, index % 12 + 1, 1),
                'customer:1', 'Fixture customer', segment, Decimal(amount)))
        return SalesSnapshot(
            as_of or date(2023 + count // 12, count % 12 + 1, 1),
            'invoice-only-fixture', tuple(sales), 0)

    def test_other_segment_only_internal_month_is_unknown_to_all_regular_analyses(self):
        for segment in ('feeding', 'unclassified'):
            with self.subTest(segment=segment):
                findings = investigate(self.snapshot(replacements={17: (segment, 500)}))
                ledger_month = next(row for row in findings['monthly']
                                    if row['month'] == '2024-06')
                # Preserve the existing monthly ledger response contract.
                self.assertEqual(ledger_month['regular_revenue'], '0.00')
                self.assertEqual(ledger_month['regular_invoices'], 0)
                self.assertEqual(ledger_month[segment + '_revenue'], '500.00')
                self.assertEqual(findings['serial_correlation']['latest_contiguous_months'], 24)
                self.assertEqual(findings['benchmarks']['status'], 'insufficient_history')
                self.assertEqual(findings['benchmarks']['available_contiguous_months'], 24)
                compared = {row['month'] for row in findings['year_over_year']}
                self.assertNotIn('2024-06', compared)
                self.assertNotIn('2025-06', compared)
                self.assertEqual(findings['analysis_periods'][
                    'unknown_regular_months_with_other_invoices'], ['2024-06'])

    def test_explicit_regular_zero_is_an_observation_and_legitimate_yoy_decline(self):
        findings = investigate(self.snapshot(replacements={17: ('regular', 0)}))
        self.assertEqual(findings['serial_correlation']['latest_contiguous_months'], 42)
        self.assertEqual(findings['benchmarks']['status'], 'research_only')
        june = next(row for row in findings['year_over_year'] if row['month'] == '2024-06')
        self.assertEqual(june['regular_revenue'], '0.00')
        self.assertEqual(june['change_percent'], -100)
        self.assertEqual(findings['analysis_periods']['observed_completed_regular_months'], 42)
        self.assertEqual(findings['analysis_periods'][
            'unknown_regular_months_with_other_invoices'], [])
        self.assertFalse(findings['coverage_verified'])

    def test_month_without_any_invoice_still_splits_regular_history(self):
        findings = investigate(self.snapshot(omitted=(17,)))
        self.assertNotIn('2024-06', {row['month'] for row in findings['monthly']})
        self.assertEqual(findings['serial_correlation']['latest_contiguous_months'], 24)
        self.assertEqual(findings['benchmarks']['available_contiguous_months'], 24)

    def test_incomplete_current_month_does_not_change_any_monthly_analysis(self):
        complete = investigate(self.snapshot())
        partial = investigate(self.snapshot(
            count=43, replacements={42: ('regular', 10_000_000)}, as_of=date(2026, 7, 15)))
        for field in ('serial_correlation', 'year_over_year', 'benchmarks'):
            self.assertEqual(partial[field], complete[field], field)
        self.assertEqual(partial['monthly'][-1]['month'], '2026-07')
        self.assertEqual(partial['monthly'][-1]['regular_revenue'], '10000000.00')
        self.assertNotEqual(partial['regular_revenue'], complete['regular_revenue'])
        self.assertEqual(partial['analysis_periods']['excluded_incomplete_months'], ['2026-07'])

    def test_trailing_other_segment_month_does_not_create_a_regular_zero(self):
        complete = investigate(self.snapshot())
        other_segment = investigate(self.snapshot(count=43, replacements={42: ('feeding', 500)}))
        for field in ('serial_correlation', 'year_over_year', 'benchmarks'):
            self.assertEqual(other_segment[field], complete[field], field)
        self.assertEqual(other_segment['monthly'][-1]['regular_revenue'], '0.00')
        self.assertEqual(other_segment['analysis_periods'][
            'unknown_regular_months_with_other_invoices'], ['2026-07'])

    def test_no_regular_observations_produces_no_regularity_or_yoy_evidence(self):
        findings = investigate(self.snapshot(
            replacements={index: ('feeding', 100 + index) for index in range(42)}))
        self.assertEqual(findings['serial_correlation']['latest_contiguous_months'], 0)
        self.assertIsNone(findings['serial_correlation']['lag_1'])
        self.assertEqual(findings['year_over_year'], [])
        self.assertEqual(findings['benchmarks']['available_contiguous_months'], 0)
        self.assertEqual(len(findings['monthly']), 42)

    def test_empty_snapshot_has_no_analysis_periods_and_uses_new_analyzer_version(self):
        findings = investigate(self.snapshot(count=0))
        self.assertEqual(ANALYZER_VERSION, 'regular-sales-patterns-v4')
        self.assertEqual(findings['analyzer_version'], ANALYZER_VERSION)
        self.assertEqual(findings['analysis_periods']['observed_completed_regular_months'], 0)
        self.assertEqual(findings['analysis_periods']['excluded_incomplete_months'], [])
        self.assertEqual(findings['monthly'], [])
