# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('account', '0026_user_last_telegram_bot_visit_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='telegram_chat_id',
            field=models.BigIntegerField(
                blank=True,
                null=True,
                verbose_name='Telegram Chat ID',
                help_text='Chat ID пользователя в Telegram для отправки уведомлений'
            ),
        ),
    ]