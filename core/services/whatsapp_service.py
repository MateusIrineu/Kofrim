import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_API_URL = 'https://graph.facebook.com/v21.0/{phone_number_id}/messages'
LIMITE_CARACTERES = 4000  # o WhatsApp aceita ate 4096 caracteres por mensagem
TIMEOUT_SEGUNDOS = 10


def _modo_real() -> bool:
    return bool(
        getattr(settings, 'WHATSAPP_ACCESS_TOKEN', '')
        and getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', '')
    )


def _simular_envio(telefone_destino, mensagem_texto):
    """Simulador local: imprime no terminal a mensagem que seria enviada ao WhatsApp."""
    bloco = (
        "\n" + "=" * 40 + "\n"
        "📱 SIMULADOR DE RESPOSTA DO WHATSAPP\n"
        f"Para: {telefone_destino}\n"
        f"Mensagem:\n{mensagem_texto}\n"
        + "=" * 40 + "\n"
    )
    try:
        print(bloco)
    except UnicodeEncodeError:
        # Console/pipe sem UTF-8 (ex.: Windows cp1252, logs ASCII): imprime sem os emojis
        print(bloco.encode('ascii', errors='replace').decode('ascii'))
    return True


def _dividir_mensagem(texto, limite=LIMITE_CARACTERES):
    """Divide textos longos (ex.: extratos) em blocos, quebrando preferencialmente em linhas."""
    if len(texto) <= limite:
        return [texto]

    blocos, atual = [], ''
    for linha in texto.split('\n'):
        while len(linha) > limite:  # linha unica gigante: corta no limite
            if atual:
                blocos.append(atual)
                atual = ''
            blocos.append(linha[:limite])
            linha = linha[limite:]
        candidato = f'{atual}\n{linha}' if atual else linha
        if len(candidato) > limite:
            blocos.append(atual)
            atual = linha
        else:
            atual = candidato
    if atual:
        blocos.append(atual)
    return blocos


def _enviar_bloco(telefone_destino, texto):
    url = GRAPH_API_URL.format(phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID)
    corpo = json.dumps({
        'messaging_product': 'whatsapp',
        'to': telefone_destino,
        'type': 'text',
        'text': {'preview_url': False, 'body': texto},
    }).encode('utf-8')
    requisicao = urllib.request.Request(
        url,
        data=corpo,
        method='POST',
        headers={
            'Authorization': f'Bearer {settings.WHATSAPP_ACCESS_TOKEN}',
            'Content-Type': 'application/json',
        },
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=TIMEOUT_SEGUNDOS) as resposta:
            return 200 <= resposta.status < 300
    except urllib.error.HTTPError as erro:
        detalhe = erro.read()[:500].decode('utf-8', errors='replace')
        logger.error("WhatsApp API respondeu %s para %s: %s", erro.code, telefone_destino, detalhe)
    except (urllib.error.URLError, TimeoutError, OSError):
        logger.exception("Falha de rede ao chamar a WhatsApp Cloud API (destino %s)", telefone_destino)
    return False


def enviar_mensagem_whatsapp(telefone_destino, mensagem_texto):
    """
    Envia uma mensagem de texto pela WhatsApp Cloud API.

    Sem WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID configurados, cai no simulador
    (impressao no terminal). Nunca levanta excecao: retorna True se tudo foi entregue
    a API e False caso contrario, para que um problema de envio nao derrube o webhook.
    """
    if not _modo_real():
        if not settings.DEBUG:
            logger.warning("WhatsApp nao configurado: mensagem para %s apenas simulada.", telefone_destino)
        return _simular_envio(telefone_destino, mensagem_texto)

    sucesso = True
    for bloco in _dividir_mensagem(str(mensagem_texto)):
        sucesso = _enviar_bloco(telefone_destino, bloco) and sucesso
    return sucesso