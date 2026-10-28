"""Offline monthly fitting, immutable shadow issues, scoring and promotion."""
from datetime import date, datetime
from decimal import Decimal

from django.db import connection, transaction
from django.utils import timezone

from accounts.models import Users
from config.data_cache import invalidate_data_cache
from .benchmarks import score
from .data import load_snapshot
from .learning import (ENGINE_VERSION, MIN_MONTHS, TARGET_PERCENT, digest, fit_model, month_end,
                       month_revision, month_start, monthly_rows, predict_model, shift_month, train)
from .models import (MonthlyCoverage, MonthlyDeployment, MonthlyForecastScore,
                     MonthlyIssuedForecast, MonthlyModelRun, MonthlyPromotion)
from .seasonal import is_sarima_artifact


def _snapshot(as_of=None):
    as_of = as_of or timezone.localdate()
    if not isinstance(as_of, date) or isinstance(as_of, datetime) or as_of > timezone.localdate():
        raise ValueError('as_of must be a business date no later than today.')
    return load_snapshot(month_start(as_of))


def valid_coverage(snapshot):
    return {c.month: c for c in MonthlyCoverage.objects.filter(month__lt=snapshot.as_of)
            if c.source_revision == month_revision(snapshot, c.month)}


def coverage_revision(coverage):
    return digest([(m.isoformat(), c.source_revision, c.confirmed_zero) for m, c in sorted(coverage.items())])


def coverage_rows(snapshot, coverage):
    return monthly_rows(snapshot, (m for m, c in coverage.items() if c.confirmed_zero))


def attest_coverage(month, username, note, confirm_zero=False):
    snapshot = _snapshot()
    if not isinstance(month, date) or isinstance(month, datetime) or month.day != 1 or month >= snapshot.as_of:
        raise ValueError('Coverage must refer to a completed month, using its first date.')
    if not note or not note.strip():
        raise ValueError('A source-reconciliation note is required.')
    reviewer = Users.objects.get(username=username, role='admin', is_active=True)
    entries = [s for s in snapshot.sales if month_start(s.day) == month]
    if any(s.segment == 'unclassified' and s.revenue != 0 for s in entries):
        raise ValueError('Resolve nonzero unclassified source entries before confirming this month.')
    zero = not any(s.segment == 'regular' for s in entries)
    if zero and not confirm_zero:
        raise ValueError('No regular sales observed; --confirm-zero is required to assert a true zero month.')
    with transaction.atomic():
        if month_revision(_snapshot(), month) != month_revision(snapshot, month):
            raise ValueError('Source changed during coverage review; retry.')
        result, created = MonthlyCoverage.objects.get_or_create(
            month=month, source_revision=month_revision(snapshot, month),
            defaults={'reviewed_by': reviewer, 'note': note.strip(), 'confirmed_zero': zero})
        if created:
            transaction.on_commit(invalidate_data_cache)
    return result, created


def _commit_lock():
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [185213771])
            # Fit outside these short locks; only revalidation/save holds writers.
            cursor.execute('LOCK TABLE sales_transaction, sales_transactionitem, sales_customer, '
                           'inventory_product, forecasting_monthlycoverage IN SHARE MODE')


def _prospective_issue(issued):
    run = issued.model_run
    artifact = run.findings.get('artifact')
    return (issued.target_month.day == 1 and
            timezone.localtime(issued.issued_at).date() == issued.target_month and
            run.created_at <= issued.issued_at and run.cutoff == issued.target_month and
            run.status == 'research_candidate' and
            run.findings.get('status') == 'research_candidate' and
            run.trained_through == month_end(shift_month(issued.target_month, -1)) and
            run.engine_version == issued.engine_version and is_sarima_artifact(artifact) and
            run.findings.get('selected_candidate') == artifact['name'] and
            artifact.get('training_months', 0) >= MIN_MONTHS and
            digest(artifact) == run.findings.get('artifact_sha256') and
            run.findings.get('next_month', {}).get('date') == issued.target_month.isoformat())


def _score_issued(snapshot, coverage):
    months = {}
    for sale in snapshot.sales:
        if sale.segment == 'regular':
            month = month_start(sale.day)
            months[month] = months.get(month, Decimal('0')) + sale.revenue
    for month, attestation in coverage.items():
        if attestation.confirmed_zero:
            months.setdefault(month, Decimal('0'))
    created = 0
    for issued in MonthlyIssuedForecast.objects.filter(engine_version=ENGINE_VERSION, target_month__lt=snapshot.as_of).select_related('model_run'):
        month = issued.target_month
        if month not in months or not _prospective_issue(issued):
            continue
        actual = months[month]
        if actual < 0:
            continue
        error = issued.value - actual
        _, new = MonthlyForecastScore.objects.get_or_create(
            issued=issued, actual_revision=month_revision(snapshot, month), coverage_verified=month in coverage,
            defaults={'actual': actual, 'error': error, 'absolute_error': abs(error),
                      'baseline_absolute_error': abs(issued.baseline_value - actual)})
        created += new
    return created


def prospective_evidence(snapshot, coverage):
    entries = []
    for issued in (MonthlyIssuedForecast.objects.filter(engine_version=ENGINE_VERSION, target_month__lt=snapshot.as_of)
                   .select_related('model_run').prefetch_related('scores').order_by('target_month')):
        if issued.target_month not in coverage or not _prospective_issue(issued):
            continue
        latest = next((s for s in issued.scores.all() if s.coverage_verified and
                       s.actual_revision == month_revision(snapshot, issued.target_month)), None)
        if latest:
            entries.append({'month': issued.target_month.isoformat(), 'actual': float(latest.actual),
                            'predicted': float(issued.value), 'baseline': float(issued.baseline_value)})
    # The acceptance evidence advances: twelve consecutive recent issued months.
    contiguous = []
    for entry in entries:
        if contiguous and date.fromisoformat(entry['month']) != shift_month(date.fromisoformat(contiguous[-1]['month'])):
            contiguous = []
        contiguous.append(entry)
    recent = contiguous[-12:]
    return {'metrics': score(recent),
            'baseline_metrics': score([{'actual': r['actual'], 'predicted': r['baseline']} for r in recent]),
            'months': [r['month'] for r in recent], 'rows': recent}


def quality_gate(run, snapshot, coverage):
    findings = run.findings
    evidence = prospective_evidence(snapshot, coverage)
    reasons = []
    if findings.get('status') != 'research_candidate':
        reasons.append('insufficient_or_invalid_training_history')
    artifact = findings.get('artifact')
    valid_artifact = is_sarima_artifact(artifact)
    try:
        valid_hash = bool(artifact) and digest(artifact) == findings.get('artifact_sha256')
    except (ValueError, TypeError, OverflowError):
        valid_hash = False
    if not valid_hash or not valid_artifact:
        reasons.append('missing_or_invalid_fitted_artifact')
    if (run.engine_version != ENGINE_VERSION or not valid_artifact or
            findings.get('selected_candidate') != artifact.get('name')):
        reasons.append('primary_model_must_be_sarima')
    target = findings.get('next_month', {}).get('date')
    if target != month_start(timezone.localdate()).isoformat():
        reasons.append('stale_training_data')
    # Require all latest twelve training/held-out months to be source-reviewed.
    if run.trained_through:
        last_month = month_start(run.trained_through)
        missing = [shift_month(last_month, -n).isoformat() for n in range(12)
                   if shift_month(last_month, -n) not in coverage]
    else:
        missing = []
    if missing or not run.trained_through:
        reasons.append('unverified_ledger_coverage')
    if not all(findings.get('retrospective_gate', {}).get(k, False)
               for k in ('below_15_percent', 'no_worse_than_baseline')):
        reasons.append('retrospective_accuracy_gate_failed')
    metrics, baseline = evidence['metrics'], evidence['baseline_metrics']
    if metrics['periods'] < 12:
        reasons.append('needs_12_prospective_reviewed_months')
    else:
        if evidence['months'][-1] != shift_month(snapshot.as_of, -1).isoformat():
            reasons.append('prospective_evidence_is_stale')
        if metrics['wape_percent'] is None or metrics['wape_percent'] >= TARGET_PERCENT:
            reasons.append('prospective_error_not_below_15_percent')
        if (metrics['wape_percent'] is None or baseline['wape_percent'] is None or
                metrics['wape_percent'] > baseline['wape_percent']):
            reasons.append('prospective_model_worse_than_baseline')
    return {'eligible': not reasons, 'reasons': reasons, 'missing_coverage_months': missing,
            'prospective': evidence, 'target_percent_exclusive': TARGET_PERCENT}


def _promote(run, gate, reason='automatic_quality_gate'):
    if not gate['eligible']:
        return False
    deployment = MonthlyDeployment.objects.select_for_update().filter(scope='regular-monthly').first()
    if deployment and deployment.model_run_id == run.pk:
        return False
    MonthlyPromotion.objects.create(model_run=run, previous_run_id=deployment.model_run_id if deployment else None,
                                    reason=reason, evidence=gate)
    MonthlyDeployment.objects.update_or_create(scope='regular-monthly', defaults={'model_run': run})
    return True


def refresh_models(as_of=None):
    requested = as_of or timezone.localdate()
    snapshot = _snapshot(requested)
    coverage = valid_coverage(snapshot)
    coverage_hash = coverage_revision(coverage)
    lookup = {'engine_version': ENGINE_VERSION, 'source_revision': snapshot.revision,
              'coverage_revision': coverage_hash, 'cutoff': snapshot.as_of}
    run = MonthlyModelRun.objects.filter(**lookup).first()
    if run is None:
        findings = train(coverage_rows(snapshot, coverage))
        findings.update(source_revision=snapshot.revision, cutoff=snapshot.as_of.isoformat(),
                        data_quality={'included_actual_invoices': len(snapshot.sales),
                                      'unclassified_invoices': sum(s.segment == 'unclassified' for s in snapshot.sales),
                                      'excluded_demo_invoices_at_fit': snapshot.excluded_demo_count,
                                      'coverage_months_at_fit': len(coverage)})
    with transaction.atomic():
        _commit_lock()
        checked = _snapshot(requested)
        checked_coverage = valid_coverage(checked)
        if checked.revision != snapshot.revision or coverage_revision(checked_coverage) != coverage_hash:
            raise ValueError('Sales or coverage changed during training; retry without saving an inconsistent model.')
        created = False
        if run is None:
            end = findings.get('training_end')
            run, created = MonthlyModelRun.objects.get_or_create(**lookup, defaults={
                'status': findings['status'], 'findings': findings,
                'trained_through': month_end(date.fromisoformat(end)) if end else None})
        scored = _score_issued(snapshot, coverage)
        issued = False
        # Never fabricate a backdated issue, or a current forecast across a gap.
        if (run.status == 'research_candidate' and requested == timezone.localdate() and
                run.findings['next_month']['date'] == requested.isoformat()):
            if digest(run.findings['artifact']) != run.findings['artifact_sha256']:
                raise ValueError('Fitted artifact checksum failed.')
            point = run.findings['next_month']
            baseline = predict_model(fit_model(run.findings['selected_baseline'], coverage_rows(snapshot, coverage)))
            _, issued = MonthlyIssuedForecast.objects.get_or_create(
                engine_version=ENGINE_VERSION, target_month=requested,
                defaults={'model_run': run, 'value': str(point['predicted_revenue']),
                          'lower_bound': str(point['lower_bound']), 'upper_bound': str(point['upper_bound']),
                          'baseline_value': str(baseline)})
        gate = quality_gate(run, snapshot, coverage)
        promoted = _promote(run, gate)
        if created or scored or issued or promoted:
            transaction.on_commit(invalidate_data_cache)
    return run, {'created': created, 'new_scores': scored, 'issued': issued, 'promoted': promoted, 'quality_gate': gate}


def activate_model(model_id):
    """Explicit activation/rollback is subject to the same data/accuracy gates."""
    with transaction.atomic():
        _commit_lock()
        snapshot = _snapshot()
        coverage = valid_coverage(snapshot)
        run = MonthlyModelRun.objects.get(pk=model_id, engine_version=ENGINE_VERSION)
        if (run.source_revision != snapshot.revision or run.coverage_revision != coverage_revision(coverage)
                or run.cutoff != snapshot.as_of):
            raise ValueError('Model source/cutoff/coverage is stale; retrain before activation.')
        gate = quality_gate(run, snapshot, coverage)
        if not gate['eligible']:
            raise ValueError('Activation withheld: ' + ', '.join(gate['reasons']))
        promoted = _promote(run, gate, reason='operator_activation_or_rollback')
        if promoted:
            transaction.on_commit(invalidate_data_cache)
    return run, promoted
