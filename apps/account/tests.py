import json
from unittest.mock import patch, MagicMock

from django.test import TestCase
from rest_framework.test import APIClient

from apps.account.models import User, SchedulerNotify
from apps.account.test_factories import InvintationFactory, VisitFactory, SchedulerNotifyFactory, \
    ConditionNotifyFactory
from apps.account.test_utils import get_auth_client


class RegisterTest(TestCase):
    auth_data = {
        "password": "SupperPassword123",
        "phone": "phone",
    }
    register_data = {
        "username": "phone",
        "first_name": "",
        "last_name": "",
    }

    def test_register(self):
        # пытаемся создать юзера
        response = self.client.post('/account/users/', data={**self.auth_data, **self.register_data})
        self.assertEqual(response.status_code, 201, response.json())
        user = User.objects.latest('id')
        self.assertTrue(user)
        self.assertEqual(user.username, self.register_data['username'])
        self.assertEqual(user.phone, self.auth_data['phone'])

        # проверяем, что не можем создать юзера с тем же телефоном
        response = self.client.post('/account/users/', data=self.auth_data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(user, User.objects.latest('id'))

        # в ответ на логин получаем токен
        response = self.client.post('/account/token/login', data=self.auth_data)
        self.assertEqual(response.status_code, 200, response.json())
        self.assertIn('auth_token', response.json())

    def test_register_with_invintation_code(self):
        # пытаемся создать юзера
        invintation = InvintationFactory(used_by=None)
        data = {**self.auth_data, **self.register_data, "code": invintation.value}
        response = self.client.post('/account/users/', data)
        self.assertEqual(response.status_code, 201)
        user = User.objects.latest('id')
        self.assertTrue(user)
        self.assertEqual(user.username, self.register_data['username'])
        self.assertEqual(user.phone, self.auth_data['phone'])
        self.assertEqual(user.used_invintation.id, invintation.id)
        invintation.refresh_from_db()
        self.assertEqual(invintation.used_by, user)


class AccountTest(TestCase):

    def setUp(self):
        self.client = APIClient()

    def test_set_device_token(self):
        # обновляем токен устрйоства пользователю
        client, user = get_auth_client(self)
        self.assertEqual(user.device_token, '')
        response = client.post('/account/device/', data={
            'token': 'some new token'
        })
        user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(user.device_token, 'some new token')

    def test_info(self):
        # проверяем наличие инфы о пользователе
        client, user = get_auth_client(self)
        visit_model = VisitFactory(user=user)
        response = client.get('/account/about/me/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        visit = data['visits'][0]
        visit.pop('id')
        visit.pop('date')
        visit.pop('duration')
        visit.pop('end')
        self.assertEqual(visit, {
            'is_free': False,
            'free_reason': None,
            'is_active': False,
            'staff': visit_model.staff.id,
        })
        self.assertEqual(data['children'], [])
        self.assertEqual(data['used_invintation'], None)
        self.assertEqual(data['my_invintations'], [])
        self.assertIn('phone', data)
        self.assertIn('username', data)
        self.assertEqual(data['phone_confirmed'], False)
        self.assertEqual(data['device_token'], '')

    @patch('apps.account.controller._send_code')
    def test_confirm_phone(self, send_code_mock):
        # подтверждение телефона
        client, user = get_auth_client(self)
        response = client.get('/account/about/me/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['phone_confirmed'], False)

        response = self.client.post('/account/confirm/', data={
            'phone': user.phone
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(send_code_mock.call_args[0][0], user.phone)
        code = send_code_mock.call_args[0][1]

        response = self.client.get('/account/confirm/', data={
            'id': data['id'],
            'code': '',
        })
        self.assertEqual(response.status_code, 403)

        response = self.client.get('/account/confirm/', data={
            'id': data['id'],
            'code': code,
        })
        self.assertEqual(response.status_code, 200)

    def test_update_info(self):
        client, user = get_auth_client(self)
        response = client.put('/account/manage/', data={
            "username": "not_a_number",
            "first_name": "Тест",
            "last_name": "Тестов",
            "email": "test@email.ru",
            "children": json.dumps([{
                "add": "",
                "data": {
                    "sex": "М",
                    "age": 10,
                    "name": "Даша",
                },
            }])
        })
        self.assertEqual(response.status_code, 200)
        resp_data = response.json()
        self.assertEqual(resp_data["username"], "not_a_number")
        self.assertEqual(resp_data["first_name"], "Тест")
        self.assertEqual(resp_data["last_name"], "Тестов")
        self.assertEqual(resp_data["email"], "test@email.ru")
        self.assertEqual(resp_data["email"], "test@email.ru")
        self.assertEqual(resp_data["children"][0]["sex"], "М")
        self.assertEqual(resp_data["children"][0]["age"], 10)
        self.assertEqual(resp_data["children"][0]["name"], "Даша")
        child_id = resp_data["children"][0]["id"]

        response = client.put('/account/manage/', data={
            "children": json.dumps([{
                "id": child_id,
                "updates": {
                    "sex": "Ж",
                    "age": 11,
                    "name": "Катя",
                },
            }])
        })
        resp_data = response.json()
        self.assertEqual(resp_data["children"][0]["sex"], "Ж")
        self.assertEqual(resp_data["children"][0]["age"], 11)
        self.assertEqual(resp_data["children"][0]["name"], "Катя")

        response = client.put('/account/manage/', data={
            "children": json.dumps([{
                "delete": True,
                "id": child_id
            }])
        })
        resp_data = response.json()
        self.assertEqual(len(resp_data["children"]), 0)

    @patch('apps.account.controller._send_code')
    def test_reset_password(self, send_code_mock):
        client, user = get_auth_client(self)
        response = client.post('/account/reset_password/', {
            'phone': user.phone,
        })
        self.assertEqual(response.status_code, 200)
        user_code = response.json()['code']
        self.assertEqual(send_code_mock.call_args[0][0], user.phone)
        code = send_code_mock.call_args[0][1]
        new_password = 'New@Password1!'
        response = client.put('/account/reset_password/', {
            'user_code': user_code,
            'code': code,
            'password': new_password
        })
        self.assertEqual(response.status_code, 200)
        response = client.post('/account/token/login', data={
            'phone': user.phone,
            'password': new_password,
        })
        self.assertEqual(response.status_code, 200)


class InviteTest(TestCase):

    def setUp(self):
        self.client = APIClient()

    def test_create_invite(self):
        client, user = get_auth_client(self)
        new_client, new_user = get_auth_client(self)

        # Создаем код для приглашения
        response = client.post('/account/invite/')
        self.assertEqual(response.status_code, 200)
        resp_data = response.json()
        self.assertEqual(resp_data['used'], False)
        self.assertEqual(resp_data['visited'], False)
        self.assertEqual(resp_data['creator'], user.id)
        invite_id, value = resp_data['id'], resp_data['value']

        # Проверяем наличие кода у себя
        response = client.get('/account/invite/')
        resp_data = response.json()
        self.assertEqual(resp_data[0]['id'], invite_id)
        self.assertEqual(resp_data[0]['value'], value)
        self.assertEqual(resp_data[0]['used'], False)
        self.assertEqual(resp_data[0]['visited'], False)
        self.assertEqual(resp_data[0]['creator'], user.id)

        # Проверяем наличие кода новым пользователем ???
        response = new_client.put('/account/use/invintation/', data={'code': value})
        resp_data = response.json()
        self.assertEqual(resp_data['id'], invite_id)
        self.assertEqual(resp_data['value'], value)
        self.assertEqual(resp_data['used'], False)
        self.assertEqual(resp_data['visited'], False)
        self.assertEqual(resp_data['creator'], user.id)


class TestScheduledNotify(TestCase):
    def setUp(self):
        self.client = APIClient()

    @patch('apps.account.tasks.send_push.apply_async')
    def test_notify(self, send_push_mock: MagicMock):
        scheduled_notify = SchedulerNotifyFactory(trigger='start', minute_offset=2)

        client, user = get_auth_client(self)
        client_admin, admin_user = get_auth_client(self, is_admin=True)

        response = client_admin.put('/mobile/visits/add/',
                                    data={'user_id': user.id, 'duration': 3600})
        self.assertEqual(response.status_code, 201)
        send_push_mock.assert_called_with(
            args=([user.id], scheduled_notify.title, scheduled_notify.body), countdown=120
        )

    def test_conditions(self):
        self.assertTrue(ConditionNotifyFactory(variable='visit.duration', value=30).compare(VisitFactory(duration=30)))
        self.assertFalse(ConditionNotifyFactory(variable='visit.duration', value=30).compare(VisitFactory(duration=40)))
        self.assertTrue(ConditionNotifyFactory(variable='visit.is_free', value=True).compare(VisitFactory(is_free=True)))
        self.assertFalse(ConditionNotifyFactory(variable='visit.is_free', value=True).compare(VisitFactory(is_free=False)))

    @patch('apps.account.tasks.send_push.apply_async')
    def test_scheduled_notify_with_condition(self, send_push_mock: MagicMock):
        scheduled_notify = SchedulerNotifyFactory(trigger='start')
        ConditionNotifyFactory(variable='visit.is_free', value=True, scheduled_notify=scheduled_notify)
        visit = VisitFactory(is_free=True)
        SchedulerNotify.send_push_for_visit(visit)
        send_push_mock.assert_called_with(
            args=([visit.user.id], scheduled_notify.title, scheduled_notify.body), countdown=0
        )

    @patch('apps.account.tasks.send_push.apply_async')
    def test_scheduled_notify_with_false_condition(self, send_push_mock: MagicMock):
        scheduled_notify = SchedulerNotifyFactory(trigger='start')
        ConditionNotifyFactory(variable='visit.is_free', value=True, scheduled_notify=scheduled_notify)
        SchedulerNotify.send_push_for_visit(VisitFactory(is_free=False))
        self.assertFalse(send_push_mock.called)

    @patch('apps.account.tasks.send_push.apply_async')
    def test_queries_count(self, send_push_mock: MagicMock):
        scheduled_notify_1 = SchedulerNotifyFactory(trigger='start')
        scheduled_notify_2 = SchedulerNotifyFactory(trigger='end')
        ConditionNotifyFactory(variable='visit.is_free', value=True,
                               scheduled_notify=scheduled_notify_1)
        ConditionNotifyFactory(variable='visit.is_free', value=True,
                               scheduled_notify=scheduled_notify_2)
        visit = VisitFactory(is_free=False)
        with self.assertNumQueries(2):
            SchedulerNotify.send_push_for_visit(visit)
