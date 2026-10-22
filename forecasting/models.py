"""Immutable investigations for the replacement forecasting system."""
from django.db import models
from django.conf import settings


class PatternStudy(models.Model):
    analyzer_version = models.CharField(max_length=50)
    source_revision = models.CharField(max_length=64)
    as_of = models.DateField()
    observed_through = models.DateField(null=True)
    findings = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(
            fields=['analyzer_version', 'source_revision', 'as_of'],
            name='forecast_pattern_revision_unique',
        )]


class MonthlyModelRun(models.Model):
    """Immutable fit/evaluation at a closed-month cutoff; no pickled code."""
    engine_version = models.CharField(max_length=60)
    source_revision = models.CharField(max_length=64)
    coverage_revision = models.CharField(max_length=64)
    cutoff = models.DateField()
    trained_through = models.DateField(null=True)
    status = models.CharField(max_length=40)
    findings = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(
            fields=['engine_version', 'source_revision', 'coverage_revision', 'cutoff'],
            name='monthly_model_revision_unique')]


class ForecastExperiment(models.Model):
    """Archived synthetic experiments; new forecasting experiments are disabled."""
    version = models.CharField(max_length=60)
    source_revision = models.CharField(max_length=64)
    findings = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['version', 'source_revision'],
                                               name='forecast_experiment_revision_unique')]


class WeeklyModelRun(models.Model):
    """Retained actual-data weekly research; never a monthly deployment."""
    engine_version = models.CharField(max_length=60)
    source_revision = models.CharField(max_length=64)
    cutoff = models.DateField()
    trained_through = models.DateField(null=True)
    status = models.CharField(max_length=40)
    findings = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['engine_version', 'source_revision', 'cutoff'],
                                               name='weekly_model_revision_unique')]


class MonthlyCoverage(models.Model):
    """Operator attestation tied to the exact observed month's contents."""
    month = models.DateField()
    source_revision = models.CharField(max_length=64)
    confirmed_zero = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    note = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['month', 'source_revision'], name='month_coverage_revision_unique')]


class MonthlyIssuedForecast(models.Model):
    """One immutable first-day prediction per engine/month, initially shadow."""
    model_run = models.ForeignKey(MonthlyModelRun, on_delete=models.PROTECT, related_name='issued')
    engine_version = models.CharField(max_length=60)
    target_month = models.DateField()
    value = models.DecimalField(max_digits=20, decimal_places=2)
    lower_bound = models.DecimalField(max_digits=20, decimal_places=2)
    upper_bound = models.DecimalField(max_digits=20, decimal_places=2)
    baseline_value = models.DecimalField(max_digits=20, decimal_places=2)
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['engine_version', 'target_month'], name='monthly_issue_engine_unique'),
            models.CheckConstraint(condition=models.Q(value__gte=0) & models.Q(lower_bound__gte=0) &
                                  models.Q(lower_bound__lte=models.F('value')) &
                                  models.Q(upper_bound__gte=models.F('value')), name='monthly_issue_valid_bounds'),
        ]


class MonthlyForecastScore(models.Model):
    """Retain old scores when a ledger correction changes the observed actual."""
    issued = models.ForeignKey(MonthlyIssuedForecast, on_delete=models.PROTECT, related_name='scores')
    actual_revision = models.CharField(max_length=64)
    coverage_verified = models.BooleanField(default=False)
    actual = models.DecimalField(max_digits=20, decimal_places=2)
    error = models.DecimalField(max_digits=20, decimal_places=2)
    absolute_error = models.DecimalField(max_digits=20, decimal_places=2)
    baseline_absolute_error = models.DecimalField(max_digits=20, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(
            fields=['issued', 'actual_revision', 'coverage_verified'], name='monthly_score_revision_unique')]


class MonthlyDeployment(models.Model):
    scope = models.CharField(max_length=40, unique=True, default='regular-monthly')
    model_run = models.ForeignKey(MonthlyModelRun, on_delete=models.PROTECT, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)


class MonthlyPromotion(models.Model):
    """Promotion/rollback trail; old fits and predictions remain available."""
    model_run = models.ForeignKey(MonthlyModelRun, on_delete=models.PROTECT, related_name='+')
    previous_run = models.ForeignKey(MonthlyModelRun, on_delete=models.PROTECT, null=True, related_name='+')
    reason = models.CharField(max_length=100)
    evidence = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class ProvisionalForecastRun(models.Model):
    """Immutable actual-data projections, separate from approved forecast issues."""
    engine_version = models.CharField(max_length=60)
    source_revision = models.CharField(max_length=64)
    findings = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['engine_version', 'source_revision'],
                                               name='provisional_source_unique')]


class ForecastRefreshJob(models.Model):
    """Tracked manual/scheduled maintenance, with one active job database-wide."""
    scope = models.CharField(max_length=20, default='all')
    status = models.CharField(max_length=16, default='queued', choices=[
        (s, s) for s in ('queued', 'running', 'succeeded', 'failed')])
    trigger = models.CharField(max_length=16, choices=[('manual', 'manual'), ('scheduled', 'scheduled')])
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    stage = models.CharField(max_length=50, default='queued')
    results = models.JSONField(default=dict)
    error_code = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ['-pk']
        constraints = [models.UniqueConstraint(fields=['scope'], condition=models.Q(status__in=['queued', 'running']),
                                               name='one_active_forecast_refresh')]


class ProjectionIssue(models.Model):
    """First saved rough prediction per origin/target; never overwritten by refits."""
    run = models.ForeignKey(ProvisionalForecastRun, on_delete=models.PROTECT, related_name='issues')
    engine_version = models.CharField(max_length=60)
    period = models.CharField(max_length=7)
    view = models.CharField(max_length=8)
    origin = models.DateField()
    target = models.DateField()
    horizon = models.PositiveIntegerField()
    value = models.DecimalField(max_digits=20, decimal_places=2)
    lower_bound = models.DecimalField(max_digits=20, decimal_places=2)
    upper_bound = models.DecimalField(max_digits=20, decimal_places=2)
    baseline_value = models.DecimalField(max_digits=20, decimal_places=2, null=True)
    evidence = models.JSONField()
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['engine_version', 'period', 'view', 'origin', 'target'],
                                               name='projection_origin_target_unique'),
                       models.CheckConstraint(condition=models.Q(value__gte=0, lower_bound__gte=0,
                           lower_bound__lte=models.F('value'), upper_bound__gte=models.F('value'),
                           target__gt=models.F('origin'), horizon__gte=1), name='projection_issue_valid_bounds')]


class ProjectionCoverage(models.Model):
    """Explicit reconciliation of a completed segment/period's exact source rows."""
    period = models.CharField(max_length=7)
    segment = models.CharField(max_length=7)
    target = models.DateField()
    actual_revision = models.CharField(max_length=64)
    confirmed_zero = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    note = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['period', 'segment', 'target', 'actual_revision'],
                                               name='projection_coverage_unique')]


class ProjectionScore(models.Model):
    """Append-only reviewed outcomes; ledger corrections retain prior scores."""
    issued = models.ForeignKey(ProjectionIssue, on_delete=models.PROTECT, related_name='scores')
    actual_revision = models.CharField(max_length=64)
    actual = models.DecimalField(max_digits=20, decimal_places=2)
    error = models.DecimalField(max_digits=20, decimal_places=2)
    absolute_error = models.DecimalField(max_digits=20, decimal_places=2)
    baseline_absolute_error = models.DecimalField(max_digits=20, decimal_places=2, null=True)
    within_bounds = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['issued', 'actual_revision'], name='projection_score_revision_unique')]
