from uuid import uuid4

from django.db import models


class CacheInvalidation(models.Model):
    """Keep failed cache rotations visible independently of the cache service."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    namespace = models.CharField(max_length=32, db_index=True)
