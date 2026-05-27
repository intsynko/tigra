import logging
import os
import asyncio
from pathlib import Path
from datetime import timedelta

from django.utils.timezone import localtime
from asgiref.sync import sync_to_async
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, PicklePersistence, PersistenceInput
)
from telegram.constants import ParseMode
from telegram.error import NetworkError, TimedOut
from telegram.request import HTTPXRequest

from django_client import DjangoClient

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Создаем директорию для хранения данных если её нет
PERSISTENCE_DIR = Path(__file__).parent / "data"
PERSISTENCE_DIR.mkdir(exist_ok=True)

# Инициализация Django клиента
django_client = DjangoClient()

CONFIRMATION_TIMEOUT_MINUTES = 10

# Обработка ошибок
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, (TimedOut, NetworkError)):
        logger.warning("Telegram API timeout/network error: %s", context.error)
        return
    logger.exception("Unhandled telegram bot error", exc_info=context.error)

# Функция для формирования главного меню
def get_main_menu(has_children: bool = False):
    keyboard = [["Создать посещение", "Список посещений"]]
    if has_children:
        keyboard.append(["Список детей"])
    keyboard.append(["Добавить ребенка"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def get_children_list_menu(children: list, selected: list = None):
    if selected is None:
        selected = []
    buttons = []
    for child in children:
        marker = "✅ " if child.id in selected else ""
        buttons.append([InlineKeyboardButton(
            f"{marker}{child.name}",
            callback_data=f"child_{child.id}"
        )])
    buttons.append([InlineKeyboardButton("Готово", callback_data="finish_selection")])
    return InlineKeyboardMarkup(buttons)


def get_cancel_keyboard():
    return ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка входящих сообщений."""
    user_id = str(update.effective_user.id)

    if "django_user" not in context.user_data:
        # сбор имени и фамилии
        if context.user_data.get("auth_state") == "awaiting_first_name":
            return await collect_first_name(update, context)
        elif context.user_data.get("auth_state") == "awaiting_last_name":
            return await collect_last_name(update, context)
        # Если пользователь не авторизован, направляем в начало
        return await start(update, context)
    else:
        # Если пользователь уже авторизован, отправляем в главное меню
        return await main_menu(update, context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало работы и запрос номера телефона."""
    user = update.effective_user

    if "django_user" in context.user_data:
        # Если пользователь уже авторизован, перенаправляем в меню
        await update.message.reply_text(
            f"Добро пожаловать снова, {user.first_name}!",
            reply_markup=get_main_menu()
        )
    else:
        # Новый пользователь
        await update.message.reply_text(
            f"Здравствуйте, {user.first_name}!\nДля работы с ботом авторизуйтесь, отправив свой номер телефона.",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("Отправить номер телефона", request_contact=True)]],
                one_time_keyboard=True,
                resize_keyboard=True
            )
        )


async def authorize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Авторизация пользователя."""
    contact = update.message.contact
    # Если пользователь не отправил номер телефона
    if not contact:
        await update.message.reply_text("Пожалуйста, отправьте ваш номер телефона.")
        return

    phone = contact.phone_number
    django_user = await django_client.get_user_by_phone(phone)
    
    if django_user:
        context.user_data["django_user"] = django_user
        await django_client.update_user_chat_id(django_user.id, update.effective_chat.id)
        user_first_name = (await django_user.user).first_name
        await update.message.reply_text(
            f"С возвращением, {user_first_name}!",
            reply_markup=get_main_menu()
        )
    else:
        context.user_data["auth_phone"] = phone
        context.user_data["auth_state"] = "awaiting_first_name"
        await update.message.reply_text("Введите ваше имя:")

    """Сбор имени пользователя."""
async def collect_first_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text in ("Создать посещение", "Список посещений", "Добавить ребенка", "Список детей"):
        await update.message.reply_text("Сначала завершите авторизацию — введите ваше имя:")
        return
    context.user_data["auth_first_name"] = text
    context.user_data["auth_state"] = "awaiting_last_name"
    await update.message.reply_text("Введите вашу фамилию:")

    """Сбор фамилии пользователя."""
async def collect_last_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text in ("Создать посещение", "Список посещений", "Добавить ребенка", "Список детей"):
        await update.message.reply_text("Сначала завершите авторизацию — введите вашу фамилию:")
        return
    phone = context.user_data["auth_phone"]
    first_name = context.user_data["auth_first_name"]
    last_name = text

    django_user = await django_client.get_or_create_user(phone, first_name, last_name)
    context.user_data["django_user"] = django_user
    await django_client.update_user_chat_id(django_user.id, update.effective_chat.id)

    del context.user_data["auth_phone"]
    del context.user_data["auth_first_name"]
    del context.user_data["auth_state"]

    await update.message.reply_text(
        "Вы успешно авторизовались!",
        reply_markup=get_main_menu()
    )


async def select_register_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    store_id = int(query.data.split('_')[2])

    django_user = context.user_data.get("django_user")
    django_user = await django_client.update_user_store(django_user, store_id)
    context.user_data["django_user"] = django_user

    await query.message.reply_text(
        "Точка сохранена!",
        reply_markup=get_main_menu()
    )

    """Выбор точки посещения."""
async def select_visit_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    store_id = int(query.data.split('_')[2])
    context.user_data["pending_store_id"] = store_id

    django_user = context.user_data.get("django_user")
    buttons = [
        [InlineKeyboardButton("1 час", callback_data="slot_60")]
    ]
    is_free_visit = await django_client.user_has_free_visit(django_user)
    if is_free_visit:
        buttons.append(
            [InlineKeyboardButton("Использовать бонусное посещение (30 минут)", callback_data="slot_30")]
        )
    cnt_to_free_visit = await django_client.user_count_to_free_visit(django_user)
    if is_free_visit:
        text = "Выберите слот времени:"
    else:
        text = f"Выберите слот времени (до бонусного визита {cnt_to_free_visit} посещения!):"
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    django_user = context.user_data.get("django_user")

    await django_client.update_last_telegram_bot_visit_date(django_user)

    if context.user_data.get("adding_child"):
        return await add_child(update, context)

    if context.user_data.get("editing_child"):
        return await edit_child(update, context)

    # Проверяем, есть ли активное посещение
    if text == "Создать посещение":
        loop = asyncio.get_event_loop()
        active_visit = await django_client.get_active_visit(django_user.id)
        if active_visit:
            end_time = await django_client.get_visit_end_time(active_visit)
            await update.message.reply_text(
                f"У вас уже есть активное посещение до {localtime(end_time).strftime('%d.%m.%Y %H:%M')}.\n"
                f"Дождитесь завершения текущего посещения перед созданием нового или обратитесь к администратору.",
                reply_markup=get_main_menu()
            )
            return

        pending_visit = await loop.run_in_executor(
            None, lambda: django_client.get_pending_visit_sync(django_user.id, CONFIRMATION_TIMEOUT_MINUTES)
        )
        if pending_visit:
            await update.message.reply_text(
                f"У вас уже есть неподтверждённое посещение от {localtime(pending_visit.date).strftime('%d.%m.%Y %H:%M')}.\n"
                f"Подтвердите посещение у администратора до {(localtime(pending_visit.date) + timedelta(minutes=10)).strftime('%d.%m.%Y %H:%M')}, иначе оно будет автоматически отменено.",
                reply_markup=get_main_menu()
            )
            return

        stores = await django_client.get_stores()
        if not stores:
            await update.message.reply_text("Нет доступных точек.")
            return
        store_buttons = [
            [InlineKeyboardButton(
                s.address,
                callback_data=f"visits_store_{s.id}"
            )]
            for s in stores
        ]
        await update.message.reply_text(
            "Выберите точку:",
            reply_markup=InlineKeyboardMarkup(store_buttons)
        )
        return

    elif text == "Список посещений":
        visits = await django_client.get_user_visits(django_user)
        cnt_to_free = await django_client.user_count_to_free_visit(django_user)
        if visits:
            visits_text = []
            for i, visit in enumerate(visits):
                children_names = await django_client.get_visit_children_names(visit)
                duration_hours = visit.duration // 3600
                if duration_hours < 1:
                    duration_str = f"{int(duration_hours * 60)} м."
                else:
                    duration_str = f"{duration_hours} ч."

                if visit.is_confirmed:
                    end_time = await django_client.get_visit_end_time(visit)
                    remaining = end_time - localtime()
                    remaining_minutes = int(remaining.total_seconds() // 60)
                    if remaining_minutes > 0:
                        status = "✅"
                        if remaining_minutes > 60:
                            remaining_str = f"{remaining_minutes // 60} ч. {remaining_minutes % 60} мин."
                        else:
                            remaining_str = f"{remaining_minutes} мин."
                        end_str = f" | Осталось: {remaining_str}"
                    else:
                        status = "🏁"
                        end_str = ""
                else:
                    status = "⏳"
                    end_str = ""

                visits_text.append(
                    f"{i + 1}. {status} {localtime(visit.date).strftime('%d.%m.%Y %H:%M')}, "
                    f"{duration_str}{end_str}, Дети: {', '.join(children_names)}"
                )
            visits_text.append(f"\nДо бонусного визита: {cnt_to_free} посещений!")
            visits_text = "\n".join(visits_text)
        else:
            visits_text = f"У вас пока нет посещений.\nДо бонусного визита: {cnt_to_free} посещений."
        await update.message.reply_text(visits_text, reply_markup=get_main_menu())

    elif text == "Список детей":
        await show_children_list(update, context)

    elif text == "Добавить ребенка":
        await update.message.reply_text("Введите имя ребенка:", reply_markup=get_cancel_keyboard())
        context.user_data["adding_child"] = True

    else:
        await update.message.reply_text("Пожалуйста, выберите действие из меню.", reply_markup=get_main_menu())

    # Показать список детей
async def show_children_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    django_user = context.user_data.get("django_user")
    children = await django_client.get_user_children(django_user)

    if not children:
        await update.message.reply_text(
            "У вас пока нет детей. Добавьте ребёнка.",
            reply_markup=get_main_menu()
        )
        return

    buttons = [
        [InlineKeyboardButton(f"{child.name} ({child.birth_date.strftime('%d.%m.%Y')})", callback_data=f"viewchild_{child.id}")]
        for child in children
    ]
    buttons.append([InlineKeyboardButton("+ Добавить ребенка", callback_data="add_child_menu")])

    await update.message.reply_text(
        "Список детей:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

    """Показать детали ребенка."""
async def show_child_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    child_id = int(query.data.split('_')[1])

    context.user_data["editing_child_id"] = child_id

    buttons = [
        [InlineKeyboardButton("✏️ Изменить имя", callback_data=f"edname_{child_id}")],
        [InlineKeyboardButton("📅 Изменить дату рождения", callback_data=f"edbirth_{child_id}")],
        [InlineKeyboardButton("🗑️ Удалить", callback_data=f"delchild_{child_id}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_children")]
    ]

    await query.message.reply_text(
        "Выберите действие:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

    """Изменение имени ребенка."""
async def edit_child_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    child_id = int(query.data.split('_')[1])

    context.user_data["editing_child"] = "name"
    context.user_data["editing_child_id"] = child_id

    await query.message.reply_text("Введите новое имя ребенка:")

    """Изменение имени ребенка."""
async def edit_child_birth_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    child_id = int(query.data.split('_')[1])

    context.user_data["editing_child"] = "birth"
    context.user_data["editing_child_id"] = child_id

    await query.message.reply_text("Введите новую дату рождения в формате ДД.ММ.ГГГГ:")

    """Изменение детали ребенка."""
async def edit_child(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    child_id = context.user_data.get("editing_child_id")
    editing_type = context.user_data.get("editing_child")

    if not child_id or not editing_type:
        await update.message.reply_text("Что-то пошло не так.", reply_markup=get_main_menu())
        return

    try:
        if editing_type == "name":
            await django_client.update_child(child_id, name=text)
            del context.user_data["editing_child"]
            del context.user_data["editing_child_id"]
            await update.message.reply_text(f"Имя изменено на {text}!", reply_markup=get_main_menu())
        elif editing_type == "birth":
            await django_client.update_child(child_id, birth_date_str=text)
            del context.user_data["editing_child"]
            del context.user_data["editing_child_id"]
            await update.message.reply_text(f"Дата рождения изменена!", reply_markup=get_main_menu())
    except ValueError:
        await update.message.reply_text("Неверный формат даты. Попробуйте еще раз (ДД.ММ.ГГГГ):")

    """Подтверждение удаления ребенка."""
async def delete_child_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    child_id = int(query.data.split('_')[1])

    await django_client.delete_child(child_id)
    await query.message.reply_text("Ребёнок удалён.", reply_markup=get_main_menu())


async def add_child(update: Update, context: ContextTypes.DEFAULT_TYPE):
    django_user = context.user_data.get("django_user")
    text = update.message.text

    if text == "Отмена":
        if "adding_child" in context.user_data:
            del context.user_data["adding_child"]
        if "child_name" in context.user_data:
            del context.user_data["child_name"]
        await update.message.reply_text("Добавление ребёнка отменено.", reply_markup=get_main_menu())
        return

    if "adding_child" in context.user_data and context.user_data["adding_child"]:
        if "child_name" not in context.user_data:
            context.user_data["child_name"] = text
            await update.message.reply_text("Введите дату рождения ребенка в формате ДД.ММ.ГГГГ:", reply_markup=get_cancel_keyboard())
        else:
            birth_date = text
            try:
                child = await django_client.add_child(
                    django_user,
                    context.user_data["child_name"],
                    birth_date
                )

                del context.user_data["adding_child"]
                del context.user_data["child_name"]

                await update.message.reply_text(
                    f"Ребенок {child.name} успешно добавлен!",
                    reply_markup=get_main_menu()
                )
            except ValueError:
                await update.message.reply_text("Неверный формат даты. Попробуйте еще раз (ДД.ММ.ГГГГ):", reply_markup=get_cancel_keyboard())


    """Выбор слота посещения."""
async def select_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    slot = int(query.data.split('_')[1])
    context.user_data["selected_slot"] = slot

    django_user = context.user_data.get("django_user")
    children = await django_client.get_user_children(django_user)

    if not children:
        await query.edit_message_text(
            "У вас нет добавленных детей. Сначала добавьте детей."
        )
        return

    context.user_data["selected_children"] = []

    await query.edit_message_text(
        "Выберите детей для посещения (можно выбрать несколько):",
        reply_markup=get_children_list_menu(children)
    )


    """Выбор участников посещения."""
async def select_participants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "finish_selection":
        await query.answer()
        if "selected_slot" not in context.user_data:
            await query.message.reply_text(
                "Пожалуйста, начните создание посещения заново /start",
                reply_markup=get_main_menu()
            )
            return

        if not context.user_data.get("selected_children"):
            await query.message.reply_text("Выберите хотя бы одного ребенка.")
            return

        django_user = context.user_data.get("django_user")
        slot = context.user_data["selected_slot"]
        children_ids = context.user_data["selected_children"]

        store_id = context.user_data.get("pending_store_id")
        if not store_id:
            await query.message.reply_text(
                "Ошибка: точка посещения не выбрана. Начните создание посещения заново /start",
                reply_markup=get_main_menu()
            )
            return

        visit = await django_client.create_visit(
            django_user, slot, children_ids, store_id
        )

        children_names = await django_client.get_visit_children_names(visit)

        if "pending_store_id" in context.user_data:
            del context.user_data["pending_store_id"]
        del context.user_data["selected_slot"]
        del context.user_data["selected_children"]

        duration_minutes = visit.duration // 60
        if duration_minutes >= 60:
            duration_str = f"{duration_minutes // 60} ч."
        else:
            duration_str = f"{duration_minutes} мин."

        context.user_data["pending_visit_id"] = visit.id

        await query.edit_message_text(
            f"Посещение создано!\n"
            f"Дата: {localtime(visit.date).strftime('%d.%m.%Y %H:%M')}\n"
            f"Продолжительность: {duration_str}\n"
            f"Игровая площадка: {visit.store.address}\n"
            f"Участники: {', '.join(children_names)}\n\n"
            f"⏳ Подтвердите посещение у администратора.\n"
            f"⏰ Через {CONFIRMATION_TIMEOUT_MINUTES} минут посещение будет отменено.",
            reply_markup=None
        )

        job_queue = context.application.job_queue
        telegram_user_id = query.from_user.id
        chat_id = query.message.chat.id
        django_user_id = django_user.id
        logging.info(
            "[CANCEL_VISIT] Scheduling cancel job for "
            f"telegram_user_id={telegram_user_id}, chat_id={chat_id}, "
            f"django_user_id={django_user_id}, visit_id={visit.id}, "
            f"delay={CONFIRMATION_TIMEOUT_MINUTES}min"
        )
        job_queue.run_once(
            cancel_unconfirmed_visit,
            when=timedelta(minutes=CONFIRMATION_TIMEOUT_MINUTES),
            data={
                "telegram_user_id": telegram_user_id,
                "chat_id": chat_id,
                "django_user_id": django_user_id,
                "visit_id": visit.id
            },
            job_kwargs={"misfire_grace_time": 60}
        )

    elif data.startswith("child_"):
        child_id = int(data.split('_')[1])
        selected_children = context.user_data.get("selected_children", [])

        if child_id in selected_children:
            selected_children.remove(child_id)
            await query.answer("Ребенок удален из списка")
        else:
            selected_children.append(child_id)
            await query.answer("Ребенок добавлен в список")

        context.user_data["selected_children"] = selected_children

        django_user = context.user_data.get("django_user")
        children = await django_client.get_user_children(django_user)
        await query.message.edit_text(
            "Выберите детей для посещения (можно выбрать несколько):",
            reply_markup=get_children_list_menu(children, selected_children)
        )

    """Отмена неподтверждённого посещения после таймаута."""
async def cancel_unconfirmed_visit(context: ContextTypes.DEFAULT_TYPE):
    # Отмена неподтверждённого посещения после таймаута
    job = context.job
    telegram_user_id = job.data["telegram_user_id"]
    chat_id = job.data["chat_id"]
    django_user_id = job.data["django_user_id"]
    visit_id = job.data["visit_id"]
    logging.info(
        "[CANCEL_VISIT] Job triggered for "
        f"telegram_user_id={telegram_user_id}, chat_id={chat_id}, "
        f"django_user_id={django_user_id}, visit_id={visit_id}"
    )

    logging.info(
        "[CANCEL_VISIT] cancel processing started for "
        f"django_user_id={django_user_id}, visit_id={visit_id}"
    )
    loop = asyncio.get_event_loop()
    pending_visit = await loop.run_in_executor(
        None, lambda: django_client.get_unconfirmed_visit_by_id_sync(visit_id, django_user_id)
    )
    logging.info(
        f"[CANCEL_VISIT] pending_visit={pending_visit}, "
        f"expected_visit_id={visit_id}, django_user_id={django_user_id}"
    )
    if pending_visit and pending_visit.id == visit_id:
        logging.info(f"[CANCEL_VISIT] Canceling visit {visit_id}")
        await loop.run_in_executor(None, lambda: django_client.cancel_visit_sync(visit_id))
        logging.info(f"[CANCEL_VISIT] Sending cancellation message to chat_id={chat_id}")
        await context.bot.send_message(
            chat_id,
            f"⏰ Ваше посещение от {localtime(pending_visit.date).strftime('%d.%m.%Y %H:%M')} было отменено "
            f"из-за отсутствия подтверждения.",
            reply_markup=get_main_menu()
        )
        logging.info(f"[CANCEL_VISIT] Message sent successfully")
    else:
        logging.info(f"[CANCEL_VISIT] Visit already confirmed or not found, skipping")

    """Подтверждение посещения."""
async def confirm_visit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Подтверждение посещения и планирование напоминаний
    query = update.callback_query
    visit_id = int(query.data.split('_')[1])

    django_user = context.user_data.get("django_user")
    visit = await django_client.confirm_visit(visit_id)
    children_names = await django_client.get_visit_children_names(visit)

    end_time = await django_client.get_visit_end_time(visit)

    duration = visit.duration // 3600
    if duration < 1:
        duration_str = f"{int(duration * 60)} м."
    else:
        duration_str = f"{duration} ч."

    await query.message.edit_text(
        f"✅ Посещение подтверждено!\n\n"
        f"Дата: {localtime(visit.date).strftime('%d.%m.%Y %H:%M')}\n"
        f"Окончание: {localtime(end_time).strftime('%d.%m.%Y %H:%M')}\n"
        f"Продолжительность: {duration_str}\n"
        f"Игровая площадка: {visit.store.address}\n"
        f"Участники: {', '.join(children_names)}\n\n"
        f"📝 Пожалуйста, заполните документы у администратора."
    )

    await query.message.reply_text(
        "Удачного посещения! 🎉",
        reply_markup=get_main_menu()
    )

    context.user_data["active_visit_id"] = visit.id
    context.user_data["active_visit_end_time"] = end_time

    job_queue = context.application.job_queue
    telegram_user_id = query.from_user.id
    chat_id = query.message.chat.id
    django_user_id = django_user.id

    minutes_before_end = (end_time - localtime()).total_seconds() // 60 - 10
    if minutes_before_end < 1:
        minutes_before_end = 1

    job_queue.run_once(
        notify_before_visit_end,
        when=timedelta(minutes=int(minutes_before_end)),
        data={
            "telegram_user_id": telegram_user_id,
            "chat_id": chat_id,
            "django_user_id": django_user_id,
            "visit_id": visit.id
        },
        job_kwargs={"misfire_grace_time": None}
    )

    minutes_to_end = (end_time - localtime()).total_seconds() // 60
    if minutes_to_end < 1:
        minutes_to_end = 1

    job_queue.run_once(
        notify_visit_end,
        when=timedelta(minutes=int(minutes_to_end)),
        data={
            "telegram_user_id": telegram_user_id,
            "chat_id": chat_id,
            "django_user_id": django_user_id,
            "visit_id": visit.id
        },
        job_kwargs={"misfire_grace_time": None}
    )

    """Напоминание о завершении посещения."""
async def notify_before_visit_end(context: ContextTypes.DEFAULT_TYPE):
    # Отправка напоминания за 10 минут до окончания посещения
    job = context.job
    telegram_user_id = job.data["telegram_user_id"]
    chat_id = job.data["chat_id"]
    django_user_id = job.data["django_user_id"]
    visit_id = job.data["visit_id"]

    loop = asyncio.get_event_loop()
    active_visit = await loop.run_in_executor(
        None, lambda: django_client.get_confirmed_active_visit_by_id_sync(visit_id, django_user_id)
    )
    if active_visit:
        end_time = await django_client.get_visit_end_time(active_visit)

        buttons = [
            [InlineKeyboardButton("Продлить на 1 час", callback_data="extend_visit")],
            [InlineKeyboardButton("Не продлевать", callback_data="end_visit")]
        ]

        await context.bot.send_message(
            chat_id,
            f"⏰ До окончания посещения осталось 10 минут!\n"
            f"Окончание: {localtime(end_time).strftime('%H:%M')}\n\nДля продления посещения обратитесь к администратору",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        logging.info(
            "[VISIT_REMINDER] Active visit not found for "
            f"telegram_user_id={telegram_user_id}, django_user_id={django_user_id}, visit_id={visit_id}"
        )


async def notify_visit_end(context: ContextTypes.DEFAULT_TYPE):
    # Отправка уведомления об окончании посещения
    job = context.job
    telegram_user_id = job.data["telegram_user_id"]
    chat_id = job.data["chat_id"]
    django_user_id = job.data["django_user_id"]
    visit_id = job.data["visit_id"]

    loop = asyncio.get_event_loop()
    active_visit = await loop.run_in_executor(
        None, lambda: django_client.get_confirmed_active_visit_by_id_sync(visit_id, django_user_id)
    )
    if not active_visit:
        logging.info(
            "[VISIT_END] Active visit not found, skip notification for "
            f"telegram_user_id={telegram_user_id}, django_user_id={django_user_id}, visit_id={visit_id}"
        )
        return

    end_time = await django_client.get_visit_end_time(active_visit)
    if localtime() < end_time:
        logging.info(
            "[VISIT_END] Visit end time moved, skip old notification for "
            f"telegram_user_id={telegram_user_id}, django_user_id={django_user_id}, visit_id={visit_id}"
        )
        return

    await context.bot.send_message(
        chat_id,
        "⏰ Время посещения закончилось.\n"
        "Спасибо, что посетили нас! 🎉\n"
        "До свидания!",
        reply_markup=get_main_menu()
    )

    """Продлить посещение на 1 час."""
async def extend_visit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.message.edit_text(
        "Для продления посещения обратитесь к администратору."
    )
    await query.message.reply_text(
        "Ждём вас! 🎉",
        reply_markup=get_main_menu()
    )

    """Отказ от продления."""
async def end_visit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    query = update.callback_query

    await query.message.edit_text(
        "Хорошо, не продлеваем посещение.\n"
        "Когда время закончится, мы отправим уведомление."
    )

    if "active_visit_id" in context.user_data:
        del context.user_data["active_visit_id"]
    if "active_visit_end_time" in context.user_data:
        del context.user_data["active_visit_end_time"]

    """Добавить ребенка из меню."""
async def add_child_from_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Запуск процесса добавления ребёнка из меню
    query = update.callback_query
    await query.answer()

    await query.message.edit_text("Введите имя ребенка:", reply_markup=get_cancel_keyboard())
    context.user_data["adding_child"] = True


def main():
    """Запуск бота."""
    # Получаем токен из переменной окружения
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("Пожалуйста, установите переменную окружения BOT_TOKEN")

    # Создаем хранилище для данных
    persistence = PicklePersistence(
        filepath=str(PERSISTENCE_DIR / "bot_data.pickle"),
        store_data=PersistenceInput(
            bot_data=True,
            chat_data=True,
            user_data=True,
            callback_data=False
        )
    )
    # Создаем объект HTTPXRequest
    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=15.0,
        connection_pool_size=20,
    )

    # Создаем приложение с поддержкой сохранения данных
    application = (
        ApplicationBuilder()
        .token(token)
        .request(request)
        .get_updates_request(request)
        .persistence(persistence)
        .build()
    )

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.CONTACT, authorize))
    application.add_handler(CallbackQueryHandler(select_slot, pattern="^slot_"))
    application.add_handler(CallbackQueryHandler(select_participants, pattern="^(child_|finish_)"))
    application.add_handler(CallbackQueryHandler(select_visit_store, pattern="^visits_store_"))
    application.add_handler(CallbackQueryHandler(select_register_store, pattern="^register_store_"))
    application.add_handler(CallbackQueryHandler(show_child_detail, pattern="^viewchild_"))
    application.add_handler(CallbackQueryHandler(edit_child_name_start, pattern="^edname_"))
    application.add_handler(CallbackQueryHandler(edit_child_birth_start, pattern="^edbirth_"))
    application.add_handler(CallbackQueryHandler(delete_child_confirm, pattern="^delchild_"))
    application.add_handler(CallbackQueryHandler(show_children_list, pattern="^back_to_children"))
    application.add_handler(CallbackQueryHandler(add_child_from_menu, pattern="^add_child_menu"))
    application.add_handler(CallbackQueryHandler(confirm_visit_handler, pattern="^confirm_"))
    application.add_handler(CallbackQueryHandler(extend_visit_handler, pattern="^extend_"))
    application.add_handler(CallbackQueryHandler(end_visit_handler, pattern="^end_visit"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    # Запускаем бота
    application.run_polling()


if __name__ == '__main__':
    main()
