"""Live alerts with persistent per-user acknowledgements and stable revisions."""
from hashlib import sha256
import json
from django.utils import timezone
from inventory.services.batch_service import BatchService
from sales.models import Order
from .models import NotificationSettings, NotificationState


def live_notifications(user):
    settings = NotificationSettings.get_config()
    visible = user.role == 'staff'
    result = []

    def add(key, kind, title, body, source, time='Live'):
        revision = sha256(json.dumps(source, sort_keys=True, default=str).encode()).hexdigest()
        result.append({'id': key, 'revision': revision, 'type': kind, 'title': title,
            'body': body, 'time': time, 'unread': True, 'dismissed': False})

    if settings.low_stock_alerts:
        for kind, rows, entity in (
            ('products', BatchService.check_product_stock(visible_to_staff=visible), 'product'),
            ('ingredients', BatchService.check_ingredient_stock(), 'ingredient'),
        ):
            if rows:
                source = sorted((row[entity].pk, str(row['remaining_quantity']), row[entity].low_stock_threshold) for row in rows)
                add(f'low-stock-{kind}', 'warning', 'Low Stock Alert', f'{len(rows)} {kind} below minimum stock', source)
    if settings.near_expiry_alerts:
        for kind, rows, entity, title in (
            ('product', BatchService.check_product_expiration(visible_to_staff=visible), 'product', 'Product Expiry Warning'),
            ('ingredient', BatchService.check_ingredient_expiration().select_related('ingredient'), 'ingredient', 'Ingredient Expiry Warning'),
        ):
            for batch in rows:
                item = getattr(batch, entity)
                add(f'expiry-{kind}-{batch.pk}', 'danger', title,
                    f'{item.name} (batch {batch.batch_number}) expires on {batch.expiration_date}',
                    [batch.pk, batch.expiration_date, batch.expiry_status])
    if settings.new_order_alerts:
        for order in Order.objects.filter(status='fulfilled').select_related('transaction').order_by('-created_at', '-pk')[:2]:
            elapsed = (timezone.localdate() - timezone.localdate(order.created_at)).days
            label = 'Today' if elapsed <= 0 else 'Yesterday' if elapsed == 1 else f'{elapsed} days ago'
            total = order.transaction.total_amount if order.transaction else 0
            add(f'fulfilled-{order.pk}', 'success', 'Order Fulfilled', f'Order #{order.pk} — ₱{total:,.2f}',
                [order.pk, order.status, total, order.created_at], label)
    return result


def notification_feed(user):
    notifications = live_notifications(user)
    states = {(state.notification_key, state.revision): state for state in
        NotificationState.objects.filter(user=user, notification_key__in=[n['id'] for n in notifications])}
    for item in notifications:
        state = states.get((item['id'], item['revision']))
        if state:
            item['unread'] = state.read_at is None
            item['dismissed'] = state.dismissed_at is not None
    return notifications
