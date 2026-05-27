import io

import xlwt

from apps.mobile.logic.selectors.visits import visits_with_end_at


def make_report() -> io.BytesIO:
    wb = xlwt.Workbook()
    sheet = wb.add_sheet('Отчет по визитам')

    header = ["Дата визита", "Продолжительность (мин)", "Бесплатный", "Причина", "Клиент", "Телефон", "Сотрудник", "Магазин"]
    for column, heading in enumerate(header):
        sheet.write(0, column, heading)

    qs = visits_with_end_at().select_related('user', 'staff', 'store')

    row_id = 1
    for visit in qs:
        duration_minutes = visit.duration // 60 if visit.duration else 0
        is_free = "Да" if visit.is_free else "Нет"
        user_name = f"{visit.user.first_name or ''} {visit.user.last_name or ''}".strip() if visit.user else ""
        user_phone = visit.user.phone if visit.user else ""
        staff_name = f"{visit.staff.first_name or ''} {visit.staff.last_name or ''}".strip() if visit.staff else ""
        store_name = visit.store.address if visit.store else ""

        sheet.write(row_id, 0, str(visit.date) if visit.date else "")
        sheet.write(row_id, 1, duration_minutes)
        sheet.write(row_id, 2, is_free)
        sheet.write(row_id, 3, visit.free_reason or "")
        sheet.write(row_id, 4, user_name)
        sheet.write(row_id, 5, user_phone)
        sheet.write(row_id, 6, staff_name)
        sheet.write(row_id, 7, store_name)
        row_id += 1

    f = io.BytesIO()
    wb.save(f)
    return f