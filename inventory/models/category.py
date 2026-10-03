from django.db import models

class Category(models.Model):
  ICON_CHOICES = [
    ('milk', 'Milk'),
    ('cheese', 'Cheese'),
    ('butter', 'Butter'),
    ('yogurt', 'Yogurt'),
    ('ice_cream', 'Ice Cream'),
    ('cream', 'Cream'),
    ('package', 'Package'),
  ]

  name = models.CharField(max_length=255)
  description = models.TextField(blank=True, null=True)
  icon = models.CharField(max_length=20, choices=ICON_CHOICES, blank=True, default='')
  is_active = models.BooleanField(default=True)
  is_visible_to_staff = models.BooleanField(default=True)
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  def __str__(self):
    return self.name
