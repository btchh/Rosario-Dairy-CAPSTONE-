from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('forecasting','0014_hierarchy_forecasts'),('inventory','0014_remove_obsolete_stock_count'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='ForecastAnalysisRun',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('engine_version',models.CharField(max_length=60)),('configuration',models.JSONField()),
            ('source_revision',models.CharField(blank=True,max_length=64)),('findings',models.JSONField(default=dict)),
            ('checksum',models.CharField(blank=True,max_length=64)),('created_at',models.DateTimeField(auto_now_add=True)),
            ('job',models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,related_name='analysis_run',to='forecasting.forecastrefreshjob'))],options={'ordering':['-pk']}),
        migrations.CreateModel(name='QualitativeForecast',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('period',models.CharField(max_length=8)),('segment',models.CharField(max_length=8)),
            ('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),
            ('product',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to='inventory.product')),
            ('created_by',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='+',to=settings.AUTH_USER_MODEL))],options={'ordering':['-updated_at','-pk']}),
        migrations.CreateModel(name='QualitativeForecastRevision',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('version',models.PositiveIntegerField()),('kind',models.CharField(max_length=12)),('targets',models.JSONField()),
            ('reason',models.TextField()),('assumptions',models.TextField(blank=True)),('expected_change',models.TextField(blank=True)),
            ('event',models.CharField(blank=True,max_length=255)),('created_at',models.DateTimeField(auto_now_add=True)),
            ('base_run',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,to='forecasting.forecastanalysisrun')),
            ('forecast',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='revisions',to='forecasting.qualitativeforecast')),
            ('created_by',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='+',to=settings.AUTH_USER_MODEL))],
            options={'ordering':['-version'],'constraints':[models.UniqueConstraint(fields=('forecast','version'),name='qualitative_forecast_version_unique')]}),
    ]
