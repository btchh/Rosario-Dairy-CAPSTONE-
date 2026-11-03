from django.db import models


class InvoiceSequence(models.Model):
    """Shared POS/order/blank-encoding counter, locked until the sale commits."""

    year = models.PositiveSmallIntegerField(primary_key=True)
    last_value = models.PositiveBigIntegerField(default=0)
