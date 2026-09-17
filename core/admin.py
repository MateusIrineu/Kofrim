from django.contrib import admin
from .models import *

@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('telefone', 'orcamento_mensal')
    search_fields = ('telefone',)

@admin.register(Meta)
class MetaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'usuario', 'valor_atual', 'valor_alvo', 'data_limite')
    list_filter = ('usuario',)

@admin.register(Transacao)
class TransacaoAdmin(admin.ModelAdmin):
    list_display = ('tipo', 'categoria', 'valor', 'usuario', 'data')
    list_filter = ('tipo', 'categoria', 'data')
    search_fields = ('descricao',)