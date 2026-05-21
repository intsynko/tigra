import os
import sys
import typing

import django
from datetime import datetime, timedelta
from asgiref.sync import sync_to_async
from django.utils.timezone import localtime

# Добавляем путь к Django проекту
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Устанавливаем настройки Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'server.settings')
django.setup()

# Импортируем модели после настройки Django
from apps.account.models import User, Child
from apps.mobile.models import Visit
from apps.stores.models import Store
from apps.mobile.visits_logic import count_to_free_visit, set_visit_if_free

class SerializableUser:
    """Класс-обертка для сериализации пользователя Django."""
    def __init__(self, user: User):
        self.id = user.id
        self.phone = user.phone
        self._user = user

    @property
    @sync_to_async
    def user(self):
        if not hasattr(self, '_loaded_user'):
            self._loaded_user = User.objects.select_related('store').get(id=self.id)
        return self._loaded_user


class DjangoClient:

    @staticmethod
    @sync_to_async
    def get_stores() -> list:
        """Получить список всех магазинов."""
        return list(Store.objects.all())

    @staticmethod
    @sync_to_async
    def get_or_create_user(
            phone: str,
            first_name: typing.Optional[str] = None,
            last_name: typing.Optional[str] = None
    ) -> SerializableUser:
        """Получить или создать пользователя."""
        phone = phone.replace('+', '')  # Убираем + если есть
        first_name = first_name or '-'
        last_name = last_name or '-'
        try:
            user = User.objects.get(phone=phone)
        except User.DoesNotExist:
            try:
                # в старой базе телефоны начинаются с 8
                user = User.objects.get(phone='8' + phone[1:])
            except User.DoesNotExist:
                user = User.objects.create(
                    first_name=first_name,
                    last_name=last_name,
                    phone=phone,
                    phone_confirmed=True,
                    source_platform='telegram_bot',
                    # Подтверждаем телефон, так как он получен через Telegram
                )
        return SerializableUser(user)

    @staticmethod
    @sync_to_async
    def get_user_by_phone(phone: str) -> typing.Optional[SerializableUser]:
        """Найти пользователя по номеру телефона."""
        phone = phone.replace('+', '')
        try:
            user = User.objects.get(phone=phone)
            return SerializableUser(user)
        except User.DoesNotExist:
            try:
                user = User.objects.get(phone='8' + phone[1:])
                return SerializableUser(user)
            except User.DoesNotExist:
                return None

    @staticmethod
    @sync_to_async
    def update_user_store(user: SerializableUser, store_id: int) -> SerializableUser:
        """Обновить магазин пользователя."""
        user_obj = User.objects.get(id=user.id)
        user_obj.store_id = store_id
        user_obj.save()
        return SerializableUser(user_obj)

    @staticmethod
    @sync_to_async
    def update_user_chat_id(user_id: int, chat_id: int) -> None:
        """Сохранить Telegram chat_id пользователя."""
        user_obj = User.objects.get(id=user_id)
        user_obj.telegram_chat_id = chat_id
        user_obj.save()

    @staticmethod
    @sync_to_async
    def get_user_children(user: SerializableUser) -> list:
        """Получить список детей пользователя."""
        return list(Child.objects.filter(my_parent_id=user.id))

    @staticmethod
    @sync_to_async
    def add_child(user: SerializableUser, name: str, birth_date_str: str) -> Child:
        """Добавить ребенка."""
        birth_date = datetime.strptime(birth_date_str, "%d.%m.%Y").date()
        age = (datetime.now().date() - birth_date).days // 365
        
        child = Child.objects.create(
            my_parent_id=user.id,
            name=name,
            birth_date=birth_date,
            age=age,
            sex='M'  # По умолчанию, можно добавить выбор пола в боте
        )
        return child

    @staticmethod
    @sync_to_async
    def create_visit(user: SerializableUser, duration_hours: int, children_ids: list, store_id: int = None) -> Visit:
        """Создать новое посещение."""
        visit = Visit(
            user_id=user.id,
            date=datetime.now(),
            duration=duration_hours * 60,  # Переводим минуты в секунды
            is_active=True,
            store_id=store_id,
            is_confirmed=False,
        )
        set_visit_if_free(visit)
        visit.save()
        if children_ids:
            visit.children.set(Child.objects.filter(id__in=children_ids))
        # Обновляем объект, чтобы получить связанные данные
        visit = Visit.objects.select_related('store').prefetch_related('children').get(id=visit.id)
        return visit

    @staticmethod
    @sync_to_async
    def get_user_visits(user: SerializableUser) -> list:
        """Получить список посещений пользователя."""
        return list(Visit.objects.filter(user_id=user.id)
                   .prefetch_related('children')
                   .order_by('-date'))

    @staticmethod
    @sync_to_async
    def user_count_to_free_visit(user: SerializableUser) -> str:
        """Сколько еще нужно до бесплатного визита."""
        user = User.objects.get(id=user.id)
        cnt = count_to_free_visit(user)[0]
        if cnt == 0:
            last_visit = Visit.objects.filter(user_id=user.id).last()
            if (localtime() - last_visit.date).days < 10:
                return 1
        return cnt

    @staticmethod
    @sync_to_async
    def user_has_free_visit(user: SerializableUser) -> bool:
        """Есть ли у пользователя бесплатный визит."""
        user = User.objects.get(id=user.id)
        if count_to_free_visit(user)[0] == 0:
            last_visit = Visit.objects.filter(user_id=user.id).last()
            if (localtime() - last_visit.date).days < 10:
                return True
        return False


    @staticmethod
    @sync_to_async
    def get_visit_children(visit: Visit) -> list:
        """Получить список детей для посещения."""
        return list(visit.children.all())

    @staticmethod
    @sync_to_async
    def get_visit_children_names(visit: Visit) -> list:
        """Получить список имен детей для посещения."""
        return [child.name for child in visit.children.all()]

    @staticmethod
    @sync_to_async
    def update_last_telegram_bot_visit_date(user: SerializableUser) -> None:
        user = User.objects.get(id=user.id)
        today = localtime().date()
        if user.last_telegram_bot_visit_date is None or user.last_telegram_bot_visit_date < today:
            user.last_telegram_bot_visit_date = today
            user.save()

    @staticmethod
    @sync_to_async
    def update_child(child_id: int, name: str = None, birth_date_str: str = None) -> Child:
        """Обновить данные ребёнка."""
        child = Child.objects.get(id=child_id)
        if name is not None:
            child.name = name
        if birth_date_str is not None:
            birth_date = datetime.strptime(birth_date_str, "%d.%m.%Y").date()
            child.birth_date = birth_date
            child.age = (datetime.now().date() - birth_date).days // 365
        child.save()
        return child

    @staticmethod
    @sync_to_async
    def delete_child(child_id: int) -> None:
        """Удалить ребёнка."""
        Child.objects.filter(id=child_id).delete()

    @staticmethod
    @sync_to_async
    def confirm_visit(visit_id: int) -> Visit:
        """Подтвердить посещение."""
        visit = Visit.objects.get(id=visit_id)
        visit.is_confirmed = True
        visit.save(update_fields=["is_confirmed"])
        return Visit.objects.select_related('store').prefetch_related('children').get(id=visit_id)

    @staticmethod
    @sync_to_async
    def cancel_visit(visit_id: int) -> None:
        """Удалить неподтверждённое посещение."""
        Visit.objects.filter(id=visit_id).delete()

    @staticmethod
    def cancel_visit_sync(visit_id: int) -> None:
        """Удалить неподтверждённое посещение (sync версия)."""
        Visit.objects.filter(id=visit_id).delete()

    @staticmethod
    @sync_to_async
    def get_pending_visit(user_id: int) -> typing.Optional[Visit]:
        """Получить неподтверждённое посещение пользователя."""
        try:
            return Visit.objects.filter(
                user_id=user_id,
                is_confirmed=False,
                is_active=True
            ).select_related('store').prefetch_related('children').first()
        except Visit.DoesNotExist:
            return None

    @staticmethod
    def get_pending_visit_sync(user_id: int) -> typing.Optional[Visit]:
        """Получить неподтверждённое посещение пользователя (sync версия)."""
        try:
            return Visit.objects.filter(
                user_id=user_id,
                is_confirmed=False,
                is_active=True
            ).select_related('store').prefetch_related('children').first()
        except Visit.DoesNotExist:
            return None

    @staticmethod
    def get_unconfirmed_visit_by_id_sync(visit_id: int, user_id: int) -> typing.Optional[Visit]:
        """Получить конкретное неподтверждённое посещение пользователя (sync версия)."""
        try:
            return Visit.objects.filter(
                id=visit_id,
                user_id=user_id,
                is_confirmed=False,
                is_active=True
            ).select_related('store').prefetch_related('children').first()
        except Visit.DoesNotExist:
            return None

    @staticmethod
    def get_confirmed_active_visit_by_id_sync(visit_id: int, user_id: int) -> typing.Optional[Visit]:
        """Получить конкретное активное подтверждённое посещение пользователя (sync версия)."""
        try:
            return Visit.objects.filter(
                id=visit_id,
                user_id=user_id,
                is_confirmed=True,
                is_active=True
            ).select_related('store').prefetch_related('children').first()
        except Visit.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def get_active_visit(user_id: int) -> typing.Optional[Visit]:
        """Получить активное подтверждённое посещение пользователя."""
        try:
            return Visit.objects.filter(
                user_id=user_id,
                is_confirmed=True,
                is_active=True
            ).select_related('store').prefetch_related('children').first()
        except Visit.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def get_visit_end_time(visit: Visit) -> datetime:
        """Получить время окончания посещения."""
        start = localtime(visit.date)
        duration_minutes = visit.duration // 60
        return start + timedelta(minutes=duration_minutes)

    @staticmethod
    @sync_to_async
    def notify_visit_confirmed(user_id: int) -> bool:
        """Отправить уведомление о подтверждении визита в Telegram."""
        import os
        import requests
        try:
            user = User.objects.get(id=user_id)
            if not user.telegram_chat_id:
                return False
            token = os.getenv("BOT_TOKEN")
            if not token:
                return False
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.post(
                url,
                params={
                    "chat_id": user.telegram_chat_id,
                    "text": "✅ Ваше посещение подтверждено!\n\nПожалуйста, заполните документы у администратора."
                },
                timeout=10
            )
            return True
        except Exception:
            return False
