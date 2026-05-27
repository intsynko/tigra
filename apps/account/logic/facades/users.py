import io

import xlwt

from apps.account.logic.selectors.users import users_with_visits, users_with_fio


def make_report() -> io.BytesIO:
    qs = (
        users_with_fio(
            users_with_visits()
        )
        .values_list(
            "fio",
            "phone",
            "email",
            "children__name",
            "children__birth_date",
            "children__age",
            "visits_count",
            "last_visit_date",
            "last_visit_store",
            "last_mobile_app_visit_date",
            "date_joined",
            "comment_from_staff",
        )
    )
    wb = xlwt.Workbook()
    sheet = wb.add_sheet('Отчет')

    header = [
        "Фио",
        "Телефон",
        "Email",
        "Имя ребенка",
        "Дата рождения ребенка",
        "Возраст ребенка",
        "Количество визитов",
        "Последний визит",
        "Последний визит точка",
        "Последний вход в мобильное приложение",
        "Дата регистрации",
        "Комментарий сотрудника",
    ]
    for column, heading in enumerate(header):
        sheet.write(0, column, heading)

    for row_id, row in enumerate(qs, start=1):
        for column, value in enumerate(row):
            sheet.write(row_id, column, str(value))

    f = io.BytesIO()
    wb.save(f)
    return f
