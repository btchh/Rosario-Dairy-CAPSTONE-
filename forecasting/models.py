from django.db import models


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
