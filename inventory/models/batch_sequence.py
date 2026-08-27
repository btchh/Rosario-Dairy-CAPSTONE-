from django.db import models

class BatchSequence(models.Model):
  """
  Serializes batch-number generation per (prefix, year, month). Locking this
  row via select_for_update() is what actually prevents two concurrent batch
  creations in the same month from generating the same sequence number —
  locking rows returned by a COUNT() query (the old approach) doesn't work,
  since select_for_update() can only lock rows that already exist, and the
  race is specifically about the *next* row that doesn't exist yet.
  """
  prefix = models.CharField(max_length=10)
  year = models.PositiveSmallIntegerField()
  month = models.PositiveSmallIntegerField()
  last_seq = models.PositiveIntegerField(default=0)

  class Meta:
    verbose_name = "Batch Sequence"
    verbose_name_plural = "Batch Sequences"
    unique_together = ('prefix', 'year', 'month')

  def __str__(self):
    return f"{self.prefix}-{self.year}{self.month:02d}: {self.last_seq}"