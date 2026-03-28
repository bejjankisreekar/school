# Generated manually for is_first_login

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_alter_user_school'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='is_first_login',
            field=models.BooleanField(default=False, help_text='If True, user must change password on next login.'),
        ),
    ]
