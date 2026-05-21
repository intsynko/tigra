import os
import logging
from datetime import timedelta
from django.utils import timezone

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from apps.mobile.models import Visit
from apps.mobile.tasks import notify_telegram_before_visit_end, notify_telegram_visit_end, _send_telegram_message

logger = logging.getLogger(__name__)

_confirmed_visits_sent = set()


def _aware_dt(dt):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _visit_end_dt(date, duration):
    if not date or not duration:
        return None
    visit_dt = _aware_dt(date)
    return visit_dt + timedelta(seconds=duration)


@receiver(post_save, sender=Visit)
def visit_confirmed_handler(sender, instance, created, **kwargs):
    if created:
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "is_confirmed" not in update_fields:
        return
    if not instance.is_confirmed or not instance.user_id:
        return

    if instance.id in _confirmed_visits_sent:
        logger.info("[VISIT_CONFIRMED] Already sent for visit_id=%s", instance.id)
        return

    if instance.date and instance.duration:
        visit_dt = _aware_dt(instance.date)
        end_dt = visit_dt + timedelta(seconds=instance.duration)
        if timezone.now() >= end_dt:
            logger.info(
                "[VISIT_CONFIRMED] Skipping notification for expired visit visit_id=%s end_dt=%s",
                instance.id, end_dt
            )
            return

    try:
        import requests
        from apps.account.models import User

        user = User.objects.get(id=instance.user_id)
        if not user.telegram_chat_id:
            logger.info(f"User {instance.user_id} has no telegram_chat_id")
            return

        token = os.getenv("BOT_TOKEN")
        if not token:
            logger.warning("BOT_TOKEN not set")
            return

        if instance.date and instance.duration:
            visit_dt = _aware_dt(instance.date)
            end_dt = visit_dt + timedelta(seconds=instance.duration)
            end_time_str = end_dt.strftime('%H:%M')
        else:
            end_time_str = None

        message_text = (
            "✅ Ваше посещение подтверждено!\n\n"
            "Пожалуйста, заполните документы у администратора."
        )
        if end_time_str:
            message_text = (
                f"✅ Ваше посещение подтверждено!\n\n"
                f"Время конца посещения: {end_time_str}\n\n"
                f"Пожалуйста, заполните документы у администратора."
            )

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        response = requests.post(
            url,
            params={
                "chat_id": user.telegram_chat_id,
                "text": message_text
            },
            timeout=10
        )
        if response.status_code != 200:
            logger.warning(
                f"Failed to send Telegram notification to user {instance.user_id}: "
                f"{response.status_code} {response.text}"
            )
        else:
            _confirmed_visits_sent.add(instance.id)
            logger.info("[VISIT_CONFIRMED] Notification sent and tracked visit_id=%s", instance.id)

        _schedule_visit_reminders(instance)
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {e}")


def _schedule_visit_reminders(instance):
    if not instance.is_confirmed or not instance.user_id:
        return
    end_dt = _visit_end_dt(instance.date, instance.duration)
    if end_dt is None:
        return
    seconds_to_end = int((end_dt - timezone.now()).total_seconds())
    logger.info(
        "[VISIT_REMINDER] Schedule check visit_id=%s user_id=%s now=%s end_dt=%s seconds_to_end=%s",
        instance.id, instance.user_id, timezone.now(), end_dt, seconds_to_end
    )
    if seconds_to_end > 0:
        seconds_before_end = seconds_to_end - 10 * 60
        if seconds_before_end > 0:
            eta_before = timezone.now() + timedelta(seconds=seconds_before_end)
            notify_telegram_before_visit_end.apply_async(
                args=[instance.id],
                eta=eta_before
            )
        else:
            logger.info(
                "[VISIT_REMINDER] Skipping before_task for visit_id=%s because seconds_before_end=%s",
                instance.id, seconds_before_end
            )
        eta_end = timezone.now() + timedelta(seconds=seconds_to_end)
        notify_telegram_visit_end.apply_async(
            args=[instance.id],
            eta=eta_end
        )
        logger.info(
            "[VISIT_REMINDER] Tasks scheduled visit_id=%s before_eta=%s end_eta=%s",
            instance.id, eta_before if seconds_before_end > 0 else None, eta_end
        )
    else:
        logger.info(
            "[VISIT_REMINDER] Skip scheduling for visit_id=%s because seconds_to_end=%s",
            instance.id, seconds_to_end
        )


_visit_old_values = {}


@receiver(pre_save, sender=Visit)
def visit_pre_save_handler(sender, instance, **kwargs):
    if instance.id is None:
        return
    try:
        old_visit = Visit.objects.get(id=instance.id)
        _visit_old_values[instance.id] = {
            "duration": old_visit.duration,
            "date": old_visit.date,
            "is_confirmed": old_visit.is_confirmed,
        }
    except Visit.DoesNotExist:
        pass


@receiver(post_save, sender=Visit)
def visit_extended_handler(sender, instance, created, **kwargs):
    if created:
        _visit_old_values.pop(instance.id, None)
        return
    old_values = _visit_old_values.pop(instance.id, None)
    if old_values is None:
        return
    if not old_values["is_confirmed"]:
        return
    if not instance.is_confirmed or not instance.user_id:
        return
    old_end_dt = _visit_end_dt(old_values["date"], old_values["duration"])
    new_end_dt = _visit_end_dt(instance.date, instance.duration)
    if old_end_dt is None or new_end_dt is None:
        return
    if new_end_dt <= old_end_dt:
        logger.info(
            "[VISIT_EXTENDED] Skip notification for visit_id=%s old_end=%s new_end=%s",
            instance.id, old_end_dt, new_end_dt
        )
        return
    logger.info(
        "[VISIT_EXTENDED] Visit extended visit_id=%s old_end=%s new_end=%s",
        instance.id, old_end_dt, new_end_dt
    )
    _schedule_visit_reminders(instance)
    _send_visit_extended_notification(instance)


def _send_visit_extended_notification(instance):
    try:
        from apps.account.models import User
        user = User.objects.get(id=instance.user_id)
        if not user.telegram_chat_id:
            logger.info(
                "[VISIT_EXTENDED] User %s has no telegram_chat_id for visit_id=%s",
                instance.user_id, instance.id
            )
            return
        end_dt = _visit_end_dt(instance.date, instance.duration)
        if end_dt is None:
            return
        end_time_str = end_dt.strftime('%H:%M')
        message_text = (
            "✅ Ваше посещение продлено!\n\n"
            f"Новое время окончания: {end_time_str}"
        )
        _send_telegram_message(user.telegram_chat_id, message_text)
        logger.info(
            "[VISIT_EXTENDED] Notification sent for visit_id=%s new_end=%s",
            instance.id, end_dt
        )
    except Exception as e:
        logger.error(f"[VISIT_EXTENDED] Error sending notification: {e}")
