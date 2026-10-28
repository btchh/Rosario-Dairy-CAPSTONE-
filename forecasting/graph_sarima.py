"""Adapt retained weekly regular SARIMA fits to the offline graph engine.

The graph's displayed history includes partial source-month boundary weeks.
The strict annual-seasonal model deliberately uses ``weekly_rows`` instead.
No fitting, writes, promotion, or approval occurs while resolving dependencies.
"""
from datetime import date, timedelta
import math

from .benchmarks import score
from .learning import digest
from .models import WeeklyModelRun
from .weekly import (BASELINES, CONFIGS, MIN_WEEKS, TEST_WEEKS, VALIDATION_WEEKS,
                     VERSION, train_weekly, valid_weekly_artifact, weekly_rows)


def _state(artifact, rows):
    return {'name': artifact['name'],
            'specification': {key: artifact[key] for key in ('order', 'seasonal_order', 'trend')},
            'scale': artifact['scale'], 'values': artifact['normalized_history'],
            'parameters': artifact['params'], 'training_start': rows[0]['date'],
            'trained_through': artifact['trained_through']}


def _valid_findings(findings, rows):
    """Bind parameters, chronology, and earlier selection to the same history."""
    try:
        artifact = findings['artifact']
        selected, baseline = findings['selected_candidate'], findings['selected_baseline']
        if not (findings['status'] == 'research_candidate'
                and findings['engine_version'] == VERSION
                and findings['selection_basis'] == 'earlier_validation_weekly_wape'
                and len(rows) >= MIN_WEEKS and findings['training_weeks'] == len(rows)
                and valid_weekly_artifact(artifact)
                and digest(artifact) == findings['artifact_sha256']
                and selected == artifact['name'] and selected in CONFIGS and baseline in BASELINES
                and artifact['training_weeks'] == len(rows)
                and artifact['trained_through'] == rows[-1]['date']
                and all(math.isclose(value * artifact['scale'], row['revenue'],
                                     rel_tol=0, abs_tol=.005)
                        for value, row in zip(artifact['normalized_history'], rows))):
            return False
        test_start = len(rows) - TEST_WEEKS
        validation_start = test_start - VALIDATION_WEEKS
        if any(findings[key] != rows[index]['date'] for key, index in (
                ('validation_start', validation_start), ('validation_end', test_start-1),
                ('test_start', test_start), ('test_end', len(rows)-1))):
            return False
        candidates = findings['candidates']
        eligible = {}
        for name, candidate in candidates.items():
            if name not in (*CONFIGS, *BASELINES):
                return False
            folds = candidate.get('validation_rows')
            if not folds:
                continue
            if not _valid_folds(folds, rows, validation_start, test_start):
                return False
            metrics = score(folds)
            if metrics != candidate.get('validation'):
                return False
            if metrics['wape_percent'] is not None:
                eligible[name] = metrics['wape_percent']
        primaries = [name for name in CONFIGS if name in eligible]
        baselines = [name for name in BASELINES if name in eligible]
        if (not primaries or not baselines
                or selected != min(primaries, key=eligible.get)
                or baseline != min(baselines, key=eligible.get)):
            return False
        # Later test outcomes validate retained evidence; they never select a model.
        for name, metrics_key in ((selected, 'selected_test'), (baseline, 'baseline_test')):
            folds = candidates[name]['test_rows']
            if (not _valid_folds(folds, rows, test_start, len(rows))
                    or score(folds) != findings[metrics_key]
                    or candidates[name].get('test') != findings[metrics_key]):
                return False
        return True
    except (KeyError, TypeError, ValueError, OverflowError, ArithmeticError, AttributeError):
        return False


def _valid_folds(folds, rows, start, end):
    if not isinstance(folds, list) or len(folds) != end-start:
        return False
    for index, fold in enumerate(folds, start):
        if (fold['date'] != rows[index]['date']
                or fold['trained_through'] != rows[index-1]['date']
                or not isinstance(fold['actual'], (int, float)) or isinstance(fold['actual'], bool)
                or not math.isfinite(fold['actual'])
                or not math.isclose(fold['actual'], rows[index]['revenue'], rel_tol=0, abs_tol=.005)
                or not isinstance(fold['predicted'], (int, float)) or isinstance(fold['predicted'], bool)
                or not math.isfinite(fold['predicted']) or not 0 <= fold['predicted'] < 1e18):
            return False
    return True


def regular_weekly_dependency(snapshot):
    """Resolve an exact-source saved fit without optimization or mutation.

    A later-retained fit may reconstruct a past target only when every training
    observation predates that target. Its retention cutoff remains disclosed;
    this never makes it an originally issued historical forecast.
    """
    rows = weekly_rows(snapshot)
    signature = digest(rows)
    base = {'rows': [{'date': row['date'], 'actual': row['revenue']} for row in rows],
            'revenue_rows': rows, 'state': None,
            'dependency': {'engine_version': VERSION, 'source_revision': snapshot.revision,
                           'training_signature': signature, 'snapshot_cutoff': snapshot.as_of.isoformat(),
                           'model_id': None, 'status': 'missing'}}
    if len(rows) < MIN_WEEKS:
        return {**base, 'status': 'insufficient_history'}
    # Inspect the newest exact-source dependency even when its status or dates
    # are corrupt. Filtering those fields out could silently select an older
    # saved fit and conceal a failed current dependency.
    run = WeeklyModelRun.objects.filter(engine_version=VERSION,
        source_revision=snapshot.revision).first()
    if run is None:
        return {**base, 'status': 'offline_training_required'}
    findings = run.findings if isinstance(run.findings, dict) else {}
    artifact = findings.get('artifact') or {}
    try:
        actual_hash = digest(artifact)
        findings_hash = digest(findings)
    except (ValueError, TypeError, OverflowError):
        actual_hash = 'invalid'
        findings_hash = 'invalid'
    base['dependency'].update(model_id=run.pk, cutoff=run.cutoff.isoformat(), status=run.status,
        artifact_hash=actual_hash, declared_hash=findings.get('artifact_sha256'),
        findings_hash=findings_hash,
        declared_training_signature=findings.get('training_signature'),
        trained_through=run.trained_through.isoformat() if run.trained_through else None)
    valid = (run.status == 'research_candidate'
             and findings.get('training_signature') == signature
             and run.trained_through == date.fromisoformat(rows[-1]['date']) + timedelta(days=6)
             and date.fromisoformat(rows[-1]['date']) + timedelta(days=6) < snapshot.as_of
             and _valid_findings(findings, rows))
    if not valid:
        return {**base, 'status': 'invalid_saved_artifact'}
    return {**base, 'state': _state(artifact, rows), 'findings': findings, 'status': 'saved_sarima'}


def regular_weekly_component(dependency, *, allow_training=True):
    """Return a graph component; fitting is reserved for offline refresh."""
    base = {'dependency': dependency['dependency'], 'model_basis': 'primary_weekly_sarima',
            'primary_model_id': dependency['dependency'].get('model_id'),
            'primary_engine_version': VERSION, 'observed_periods': len(dependency['rows']),
            'missing_periods': 0, 'test': None,
            'selected_family_policy': 'SARIMA family requested; configuration selected by earlier validation only'}
    if dependency['status'] == 'insufficient_history':
        return {**base, 'status': 'fallback_allowed', 'model_basis': 'provisional_fallback',
                'fallback_reason': 'annual_weekly_sarima_requires_130_contiguous_completed_weeks',
                'seasonal_terms_enabled': False}
    if dependency['status'] not in ('saved_sarima', 'offline_training_required'):
        return {**base, 'status': 'invalid_primary_artifact'}
    reuse = dependency['status'] == 'saved_sarima'
    if not reuse and not allow_training:
        return {**base, 'status': 'primary_refresh_required'}
    findings = dependency.get('findings') if reuse else train_weekly(dependency['revenue_rows'])
    if not _valid_findings(findings, dependency['revenue_rows']):
        return {**base, 'status': 'invalid_primary_artifact' if reuse else 'primary_model_unavailable'}
    selected = findings['selected_candidate']
    return {**base, 'status': 'provisional', 'state': _state(findings['artifact'], dependency['revenue_rows']),
            'seasonal_terms_enabled': True,
            'selection': ('reuse_primary_weekly_sarima_selection' if reuse else
                          'earlier_validation_weekly_sarima_selection'),
            'validation_basis': 'rolling_one_week_ahead',
            'validation': findings['candidates'][selected]['validation'],
            'primary_weekly_backtest': {
                'basis': 'rolling_one_week_ahead', 'metrics': findings['selected_test'],
                'baseline_metrics': findings['baseline_test'],
                'selected_baseline': findings['selected_baseline'],
                **{key: findings[key] for key in ('validation_start', 'validation_end', 'test_start', 'test_end')}},
            'limitations': [
                'The chosen SARIMA family is a product decision, not a claim that it beats every baseline.',
                'Rolling one-week errors do not validate a long-range or annual forecast path.',
                'Fitted seasonal effects may be weak; forecast curvature is not manufactured.',
                'Invoice-bearing weeks are not independently verified demand or complete ledger coverage.']}
