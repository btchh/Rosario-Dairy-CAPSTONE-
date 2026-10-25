from django.db import models


class StockReturn(models.Model):
    """Actual cancellation outcome per sold line; zero means no stock restored.

    Keep the event even for a skipped return so the ledger retains the original
    physical deduction. Legacy cancellations have no inferred return event.
    """
    sale_item = models.OneToOneField('TransactionItem', on_delete=models.PROTECT,
                                    related_name='stock_return')
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gte=0),
                                              name='sale_stock_return_nonnegative')]
