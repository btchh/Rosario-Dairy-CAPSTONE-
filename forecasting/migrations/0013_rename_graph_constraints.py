from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('forecasting', '0012_graphforecastrun_graphprediction'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='graphforecastrun', name='beta_graph_revision_unique',
        ),
        migrations.RemoveConstraint(
            model_name='graphprediction', name='beta_graph_issue_unique',
        ),
        migrations.RemoveConstraint(
            model_name='graphprediction', name='beta_graph_issue_nonnegative',
        ),
        migrations.AddConstraint(
            model_name='graphforecastrun',
            constraint=models.UniqueConstraint(
                fields=['engine_version', 'scope', 'period', 'as_of', 'source_revision'],
                name='forecast_graph_revision_unique',
            ),
        ),
        migrations.AddConstraint(
            model_name='graphprediction',
            constraint=models.UniqueConstraint(
                fields=['run', 'view', 'target'], name='forecast_graph_issue_unique',
            ),
        ),
        migrations.AddConstraint(
            model_name='graphprediction',
            constraint=models.CheckConstraint(
                condition=models.Q(value__gte=0), name='forecast_graph_issue_nonnegative',
            ),
        ),
    ]
