# Generated for Kofrim Stage 2 (Idempotency)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_corrige_data_e_categoria_saude'),
    ]

    operations = [
        migrations.CreateModel(
            name='MensagemProcessada',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('message_id', models.CharField(max_length=120, unique=True)),
                ('telefone', models.CharField(blank=True, default='', max_length=20)),
                ('processado_em', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
