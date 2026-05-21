import logging
import os
from datetime import timedelta

import requests
from django.utils.timezone import is_naive, make_aware, now as tz_now

from apps.mobile.models import Visit
from server.tasks import app


logger = logging.getLogger(__name__)


@app.task()
def clear_not_confirmed_visits():
    Visit.objects.filter(is_confirmed=False).delete()


def _send_telegram_message(chat_id: int, text: str) -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.warning("BOT_TOKEN not set, telegram message skipped")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url,
        params={
            "chat_id": chat_id,
            "text": text
        },
        timeout=10
    )
    if response.status_code != 200:
        logger.warning(
            "Failed to send Telegram message to chat_id=%s: %s %s",
            chat_id, response.status_code, response.text
        )


def _visit_end_dt(visit: Visit):
    if not visit.date:
        return None
    visit_dt = visit.date
    if is_naive(visit_dt):
        visit_dt = make_aware(visit_dt)
    return visit_dt + timedelta(seconds=visit.duration or 0)


@app.task()
def notify_telegram_before_visit_end(visit_id: int):
    logger.info("[VISIT_REMINDER] notify_before task started visit_id=%s", visit_id)
    try:
        visit = Visit.objects.select_related("user").get(id=visit_id)
    except Visit.DoesNotExist:
        logger.info("[VISIT_REMINDER] visit not found in notify_before visit_id=%s", visit_id)
        return

    if not visit.is_confirmed or not visit.user or not visit.user.telegram_chat_id:
        logger.info(
            "[VISIT_REMINDER] notify_before skip flags visit_id=%s is_confirmed=%s has_user=%s has_chat_id=%s",
            visit_id, visit.is_confirmed, bool(visit.user), bool(visit.user and visit.user.telegram_chat_id)
        )
        return

    end_dt = _visit_end_dt(visit)
    if not end_dt:
        logger.info("[VISIT_REMINDER] notify_before skip no end_dt visit_id=%s", visit_id)
        return

    if tz_now() >= end_dt:
        logger.info("[VISIT_REMINDER] notify_before skip already ended visit_id=%s now=%s end_dt=%s", visit_id, tz_now(), end_dt)
        return

    seconds_to_end = int((end_dt - tz_now()).total_seconds())

    if seconds_to_end > 10 * 60:
        eta = tz_now() + timedelta(seconds=seconds_to_end - 10 * 60)
        notify_telegram_before_visit_end.apply_async(
            args=[visit_id],
            eta=eta
        )
        logger.info(
            "[VISIT_REMINDER] notify_before rescheduled visit_id=%s seconds_to_end=%s eta=%s",
            visit_id, seconds_to_end, eta
        )
        return

    _send_telegram_message(
        visit.user.telegram_chat_id,
        f"⏰ До окончания посещения осталось 10 минут!\nОкончание: {end_dt.astimezone().strftime('%H:%M')}\n\nДля продления посещения обратитесь к администратору"
    )
    logger.info("[VISIT_REMINDER] notify_before message sent visit_id=%s", visit_id)


@app.task()
def notify_telegram_visit_end(visit_id: int):
    logger.info("[VISIT_END] notify_end task started visit_id=%s", visit_id)
    try:
        visit = Visit.objects.select_related("user").get(id=visit_id)
    except Visit.DoesNotExist:
        logger.info("[VISIT_END] visit not found in notify_end visit_id=%s", visit_id)
        return

    if not visit.is_confirmed or not visit.user or not visit.user.telegram_chat_id:
        logger.info(
            "[VISIT_END] notify_end skip flags visit_id=%s is_confirmed=%s has_user=%s has_chat_id=%s",
            visit_id, visit.is_confirmed, bool(visit.user), bool(visit.user and visit.user.telegram_chat_id)
        )
        return

    end_dt = _visit_end_dt(visit)
    if not end_dt:
        logger.info("[VISIT_END] notify_end skip no end_dt visit_id=%s", visit_id)
        return

    if tz_now() < end_dt:
        eta = tz_now() + timedelta(seconds=(end_dt - tz_now()).total_seconds())
        notify_telegram_visit_end.apply_async(
            args=[visit_id],
            eta=eta
        )
        logger.info(
            "[VISIT_END] notify_end rescheduled visit_id=%s eta=%s",
            visit_id, eta
        )
        return

    seconds_since_end = int((tz_now() - end_dt).total_seconds())
    if seconds_since_end > 10 * 60:
        logger.info(
            "[VISIT_END] notify_end skip too old visit_id=%s seconds_since_end=%s",
            visit_id, seconds_since_end
        )
        return

    _send_telegram_message(
        visit.user.telegram_chat_id,
        "⏰ Время посещения закончилось.\nСпасибо, что посетили нас! 🎉\nДо свидания!"
    )
    logger.info("[VISIT_END] notify_end message sent visit_id=%s", visit_id)
