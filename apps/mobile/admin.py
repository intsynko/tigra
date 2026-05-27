import datetime

from django.contrib import admin
from django.http import HttpResponse
from django.utils.safestring import mark_safe
from django.utils.timezone import localtime
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions
from rangefilter.filters import DateRangeFilter

from apps.mobile.logic.facades.visits import make_report as visits_make_report
from apps.mobile.logic.selectors.visits import visits_with_end_at
from apps.mobile.models import Visit
from apps.stores.models import Store
from apps.account.admin.tools import model_admin_url


class ActiveVisitFilter(admin.SimpleListFilter):
    title = 'Активный визит'
    parameter_name = 'is_active_visit'

    def lookups(self, request, model_admin):
        return (
            ('yes', _('Yes')),
            ('no', _('No')),
        )

    def queryset(self, request, queryset):
        if self.value() is None:
            return queryset
        if self.value() == 'yes':
            return queryset.filter(
                end_at__gte=localtime(),
            )
        else:
            return queryset.filter(
                end_at__lt=localtime(),
            )

class VisitAdmin(DjangoObjectActions, admin.ModelAdmin):
    readonly_fields = ("date", "duration", "is_free", "free_reason", "user_", "staff_", "store")
    fields = ("date", "duration", "is_free", "free_reason", "user_", "staff_", "store")
    list_display = ("date", "visit_end", "user_", "children_", "store")
    changelist_actions = ('make_report',)

    list_filter = (ActiveVisitFilter, ("date", DateRangeFilter), "is_free", "free_reason", "store")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return visits_with_end_at(queryset).prefetch_related("children")

    def make_report(self, request, obj):
        file = visits_make_report()
        response = HttpResponse(file.getvalue(), content_type='application/vnd.ms-excel')
        response['Content-Disposition'] = 'attachment; filename="visits_report.xls"'
        return response

    make_report.label = "Сгенерировть отчет"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "store":
            if not request.user.is_superuser and request.user.store:
                kwargs["queryset"] = Store.objects.filter(id=request.user.store.id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def visit_end(self, obj: Visit):
        if obj.duration is None:
            return '-'
        if obj.duration >= 11 * 60 * 60:
            return 'до конца дня'
        return localtime(obj.end_at).time()
    visit_end.admin_order_field = 'visit_end'
    visit_end.short_description = 'Конец визита'

    def children_(self, obj: Visit):
        children = obj.children.all()

        if len(children):
            return mark_safe("<br/>".join([child.admin_str() for child in children]))
        else:
            return "-"
    children_.admin_order_field = 'children'
    children_.short_description = 'Дети'
    children_.allow_tags = True

    def user_(self, obj):
        return model_admin_url(obj.user)

    def staff_(self, obj):
        return model_admin_url(obj.staff)


admin.site.register(Visit, VisitAdmin)
