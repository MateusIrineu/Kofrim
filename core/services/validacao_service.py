from datetime import datetime
from decimal import Decimal, InvalidOperation

from core.models import Transacao

CATEGORIAS_VALIDAS = {c[0] for c in Transacao.CATEGORIAS}
TIPOS_VALIDOS = {'receita', 'despesa'}
VALOR_MAXIMO = Decimal('99999999.99')  # limite do DecimalField(max_digits=10, decimal_places=2)


def parse_valor(bruto):
    """Converte para Decimal positivo com 2 casas. Retorna None se invalido."""
    if bruto is None or isinstance(bruto, bool):
        return None
    try:
        valor = Decimal(str(bruto).replace(',', '.'))
    except (InvalidOperation, ValueError):
        return None
    if not valor.is_finite() or valor <= 0 or valor > VALOR_MAXIMO:
        return None
    return valor.quantize(Decimal('0.01'))


def parse_data(bruto):
    """Converte 'YYYY-MM-DD' em date. Retorna None se ausente ou invalida."""
    if not bruto:
        return None
    try:
        return datetime.strptime(str(bruto), '%Y-%m-%d').date()
    except ValueError:
        return None


def parse_id(bruto):
    try:
        valor = int(bruto)
    except (TypeError, ValueError):
        return None
    return valor if valor > 0 else None


def normalizar_categoria(bruto):
    categoria = str(bruto or '').strip().lower()
    return categoria if categoria in CATEGORIAS_VALIDAS else 'outros'


def validar_transacao(dados):
    """
    Valida e normaliza uma despesa/receita.
    Retorna (dados_limpos, None) em caso de sucesso ou (None, mensagem_de_erro).
    """
    tipo = dados.get('tipo')
    if tipo not in TIPOS_VALIDOS:
        return None, "Nao entendi se foi uma despesa ou uma receita."

    valor = parse_valor(dados.get('valor'))
    if valor is None:
        return None, "Nao consegui entender o valor. Tente algo como: 'Gastei 45 de pizza'."

    descricao = str(dados.get('descricao') or '').strip()[:200] or 'Sem descricao'

    return {
        'tipo': tipo,
        'valor': valor,
        'categoria': normalizar_categoria(dados.get('categoria')),
        'descricao': descricao,
        'data': parse_data(dados.get('data')),
    }, None
