from django.db import models


class SimulationRun(models.Model):
    """Generated scenarios, deliberately separate from observed transactions."""

    target_year = models.PositiveIntegerField()
    seed = models.PositiveIntegerField()
    method = models.CharField(max_length=80)
    source_signature = models.CharField(max_length=64)
    metadata = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['target_year', 'seed', 'method', 'source_signature'],
            name='forecast_simulation_run_unique',
        )]


class SimulatedSalesDay(models.Model):
    run = models.ForeignKey(SimulationRun, on_delete=models.CASCADE, related_name='days')
    date = models.DateField()
    feeding_revenue = models.DecimalField(max_digits=20, decimal_places=2)
    other_sales_revenue = models.DecimalField(max_digits=20, decimal_places=2)

    class Meta:
        ordering = ['date']
        constraints = [
            models.UniqueConstraint(fields=['run', 'date'], name='forecast_simulation_day_unique'),
            models.CheckConstraint(
                condition=models.Q(feeding_revenue__gte=0, other_sales_revenue__gte=0),
                name='forecast_simulation_nonnegative',
            ),
        ]


class ForecastRun(models.Model):
    scope=models.CharField(max_length=80)
    version=models.CharField(max_length=40)
    configuration_signature=models.CharField(max_length=64)
    source_signature=models.CharField(max_length=64)
    data_end=models.DateField()
    result=models.JSONField()
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering=['-pk']


class IssuedForecast(models.Model):
    scope=models.CharField(max_length=80)
    version=models.CharField(max_length=40)
    configuration_signature=models.CharField(max_length=64)
    setup=models.CharField(max_length=40)
    period=models.CharField(max_length=10)
    date=models.DateField()
    trained_through=models.DateField()
    payload=models.JSONField()
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['scope','version','configuration_signature','setup','period','date'],name='hybrid_issue_unique'),
            models.CheckConstraint(condition=models.Q(trained_through__lt=models.F('date')),name='hybrid_issue_past_only')]
