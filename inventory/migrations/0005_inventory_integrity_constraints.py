from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0004_category_is_visible_to_staff'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ingredientbatch',
            name='date_received',
            field=models.DateField(default=django.utils.timezone.localdate),
        ),
        migrations.AlterField(
            model_name='ingredient',
            name='low_stock_threshold',
            field=models.IntegerField(default=10, validators=[MinValueValidator(0)]),
        ),
        migrations.AlterField(
            model_name='ingredient',
            name='shelf_life',
            field=models.IntegerField(validators=[MinValueValidator(0)]),
        ),
        migrations.AlterField(
            model_name='product',
            name='low_stock_threshold',
            field=models.IntegerField(default=10, validators=[MinValueValidator(0)]),
        ),
        migrations.AlterField(
            model_name='product',
            name='shelf_life',
            field=models.IntegerField(validators=[MinValueValidator(0)]),
        ),
        migrations.AlterField(
            model_name='productbatch',
            name='date_received',
            field=models.DateField(default=django.utils.timezone.localdate),
        ),
        migrations.AlterField(
            model_name='stockcount',
            name='count_date',
            field=models.DateField(default=django.utils.timezone.localdate),
        ),
        migrations.AddConstraint(
            model_name='stockadjustment',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(('ingredient_batch__isnull', True), ('product_batch__isnull', False)) |
                    models.Q(('ingredient_batch__isnull', False), ('product_batch__isnull', True))
                ),
                name='stock_adjustment_exactly_one_batch',
            ),
        ),
        migrations.AddConstraint(
            model_name='stockcount',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(('ingredient_batch__isnull', True), ('product_batch__isnull', False)) |
                    models.Q(('ingredient_batch__isnull', False), ('product_batch__isnull', True))
                ),
                name='stock_count_exactly_one_batch',
            ),
        ),
    ]
