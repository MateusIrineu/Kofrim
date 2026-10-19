from django.db import models
from django.utils import timezone

class Usuario(models.Model):
    telefone = models.CharField(max_length=20, unique=True, help_text=" Ex: 5584999999999")
    orcamento_mensal = models.DecimalField(max_digits=10, decimal_places=2, help_text="Sua receita livre do mês.")

    def __str__(self):
        return self.telefone

class Meta(models.Model):
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='metas')

    titulo = models.CharField(max_length=100, help_text="Ex: R$ 5.000 reais")
    valor_alvo = models.DecimalField(max_digits=10, decimal_places=2)
    valor_atual = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    data_limite = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.titulo} - R$ {self.valor_atual} / R$ {self.valor_alvo}"

class Transacao(models.Model):
    TIPO_CHOICES = [
        ('receita', 'Receita'),
        ('despesa', 'Despesa')
    ]

    CATEGORIAS = [
        ('alimentacao', 'Alimentação'),   # restaurantes, delivery, mercado
        ('saude',       'Saúde'),          # farmácia, consultas, exames, plano de saúde
        ('servicos',    'Serviços'),       # academia, streaming, contas, assinaturas
        ('transporte',  'Transporte'),     # Uber, combustível, ônibus
        ('lazer',       'Lazer'),          # cinema, shows, viagens, hobbies
        ('outros',      'Outros'),
    ]

    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='transacoes')
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default='despesa')
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    categoria = models.CharField(max_length=30, choices=CATEGORIAS, default='outros')
    descricao = models.CharField(max_length=200, help_text="Ex: Ifood, Uber, Luz")

    data = models.DateField(default=timezone.localdate)

    def __str__(self):
        return f"{self.tipo.capitalize()} - {self.categoria}: R$ {self.valor} ({self.data.strftime('%d/%m')})"


class MensagemProcessada(models.Model):
    message_id = models.CharField(max_length=120, unique=True)
    telefone = models.CharField(max_length=20, blank=True, default='')
    processado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.message_id} ({self.telefone})"