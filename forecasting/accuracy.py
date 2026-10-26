"""Read-only accuracy evidence. Never fit, issue, promote, or alter sales."""
import math
from datetime import date

from django.utils import timezone

from .benchmarks import score
from .learning import ENGINE_VERSION, TEST_MONTHS, digest, shift_month
from .lifecycle import (coverage_revision, coverage_rows, quality_gate,
                        valid_coverage)
from .data import load_snapshot
from .models import MonthlyModelRun
from .seasonal import is_sarima_artifact
from .weekly import weekly_report


def error_summary(rows):
    """Currency errors and dispersion; percentage error is undefined at zero."""
    errors = []
    normalized = []
    for row in rows:
        actual, predicted = float(row['actual']), float(row['predicted'])
        if not all(math.isfinite(v) and v >= 0 for v in (actual, predicted)):
            raise ValueError('Accuracy requires finite nonnegative actuals and predictions.')
        errors.append(predicted - actual)
        normalized.append({'actual': actual, 'predicted': predicted})
    metrics = score(normalized)
    mean_error = sum(errors) / len(errors) if errors else None
    total = sum(abs(float(row['actual'])) for row in rows)
    return {**metrics,
            'rmse': round(math.sqrt(sum(e*e for e in errors)/len(errors)), 2) if errors else None,
            'error_stddev': round(math.sqrt(sum((e-mean_error)**2 for e in errors)/len(errors)), 2) if errors else None,
            'max_absolute_error': round(max(map(abs, errors)), 2) if errors else None,
            'signed_bias_percent': round(100*sum(errors)/total, 4) if total else None}


def compare_errors(rows, baseline_rows):
    # A skill score is meaningful only for exactly the same targets and actuals.
    if len(rows) != len(baseline_rows) or any(
            a.get('month', a.get('date')) != b.get('month', b.get('date'))
            or a['actual'] != b['actual'] for a, b in zip(rows, baseline_rows)):
        raise ValueError('Model and baseline must use identical targets and actuals.')
    model, baseline = error_summary(rows), error_summary(baseline_rows)
    model_error = sum(abs(r['predicted']-r['actual']) for r in rows)
    baseline_error = sum(abs(r['predicted']-r['actual']) for r in baseline_rows)
    return {'model': model, 'baseline': baseline,
            'absolute_error_reduction_percent': round(100*(1-model_error/baseline_error), 4)
                if baseline_error else None}


def monthly_accuracy(snapshot):
    coverage = valid_coverage(snapshot)
    rows = coverage_rows(snapshot, coverage)
    result = {'status': 'refresh_required', 'engine_version': ENGINE_VERSION,
              'training_months': len(rows), 'training_start': rows[0]['month'] if rows else None,
              'training_end': rows[-1]['month'] if rows else None,
              'reviewed_months': len(coverage), 'comparison': None}
    run = MonthlyModelRun.objects.filter(engine_version=ENGINE_VERSION,
        source_revision=snapshot.revision, coverage_revision=coverage_revision(coverage),
        cutoff=snapshot.as_of).first()
    if run is None:
        return result
    findings = run.findings
    result.update(model_id=run.pk, status=run.status,
                  selected_candidate=findings.get('selected_candidate'),
                  selected_baseline=findings.get('selected_baseline'),
                  quality_gate=quality_gate(run, snapshot, coverage))
    if run.status != 'research_candidate':
        return result
    artifact = findings.get('artifact') or {}
    candidates = findings.get('candidates', {})
    selected = candidates.get(findings.get('selected_candidate'), {}).get('test_rows', [])
    baseline = candidates.get(findings.get('selected_baseline'), {}).get('test_rows', [])
    try:
        if (not is_sarima_artifact(artifact) or digest(artifact) != findings.get('artifact_sha256')
                or artifact['name'] != findings.get('selected_candidate')
                or len(selected) != TEST_MONTHS or len(baseline) != TEST_MONTHS
                or len(rows) < TEST_MONTHS):
            raise ValueError('Incomplete fitted model or comparison.')
        for folds in (selected, baseline):
            for fold, actual in zip(folds, rows[-TEST_MONTHS:]):
                if (fold['month'] != actual['month'] or
                        fold['trained_through'] != shift_month(date.fromisoformat(fold['month']), -1).isoformat()
                        or abs(float(fold['actual'])-actual['revenue']) > .005):
                    raise ValueError('Historical comparison does not match the current ledger.')
        result['comparison'] = compare_errors(selected, baseline)
    except (ValueError, TypeError, KeyError, OverflowError):
        result.update(status='invalid_comparison', comparison=None)
        return result
    result.update(status='retrospective_only', test_start=selected[0]['month'],
                  test_end=selected[-1]['month'],
                  months_since_training=(snapshot.as_of.year-date.fromisoformat(rows[-1]['month']).year)*12
                    +snapshot.as_of.month-date.fromisoformat(rows[-1]['month']).month-1,
                  uncertainty='Historical absolute-error band; no validated future coverage probability.')
    return result


def accuracy_report():
    today = timezone.localdate()
    snapshot = load_snapshot(today.replace(day=1))
    weekly = weekly_report()
    weekly_result = {key: weekly.get(key) for key in
                     ('status', 'model_id', 'selected_candidate', 'selected_baseline',
                      'metrics', 'baseline_metrics', 'beats_baseline', 'evaluation')}
    return {'as_of': today.isoformat(), 'scope': 'real', 'fits_performed': 0,
            'regular_monthly': monthly_accuracy(snapshot), 'regular_weekly': weekly_result,
            'data_quality': {'excluded_demo_invoices': snapshot.excluded_demo_count,
                'unclassified_invoices': sum(s.segment == 'unclassified' for s in snapshot.sales)},
            'limitations': [
                'Historical tests use corrected ledger history, not proof of what was known at the time.',
                'Historical WAPE is not an accuracy percentage or proof of future performance.',
                'Error standard deviation describes test errors, not a calibrated forecast interval.',
                'One-month rolling errors do not establish multi-month or yearly forecast accuracy.',
                'Missing periods are unknown; only reviewed periods establish ledger completeness.',
                'Feeding and Combined require separate evidence; regular-sales scores do not apply to them.',
            ]}
