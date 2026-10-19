import hashlib
import hmac
import json
import logging
import threading
from datetime import date, timedelta
from django.conf import settings
from django.db import IntegrityError, connection
from django.db.models import Sum
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

#core
from core.services.gemini_service import analisar_mensagem
from core.models import Usuario, Transacao, MensagemProcessada
from core.services.formatacao_service import formatar_valor
from core.services.whatsapp_service import enviar_mensagem_whatsapp
from core.services import meta_service
from core.services.validacao_service import (
    validar_transacao, parse_valor, parse_data, parse_id, normalizar_categoria,
)

logger = logging.getLogger(__name__)

# --- Mensagens fixas ---

MENSAGEM_AJUDA = (
    "Ola! Eu sou o *Kofrim*, seu assistente financeiro no WhatsApp.\n\n"
    "Voce pode me escrever de forma natural. Exemplos:\n\n"
    "*Registrar*\n"
    "_'Gastei 45 de pizza'_\n"
    "_'Farmacia 80 ontem'_\n"
    "_'Recebi 1500 de salario'_\n\n"
    "*Consultar*\n"
    "_'Qual meu saldo?'_\n"
    "_'Extrato dos ultimos 2 meses'_\n\n"
    "*Metas e orcamento*\n"
    "_'Quero juntar 3000 ate dezembro'_\n"
    "_'Guardei 200 na minha meta'_\n"
    "_'Como esta minha meta?'_\n"
    "_'Meu orcamento mensal e 5000'_\n\n"
    "*Corrigir*\n"
    "_'Apaga o registro 12'_\n"
    "_'Muda o valor do registro 3 para 90'_"
)
MENSAGEM_SO_TEXTO = (
    "Por enquanto eu so consigo entender mensagens de *texto*. "
    "Pode me escrever o que voce gastou ou recebeu? Ex.: _'Gastei 45 de pizza'_"
)
MENSAGEM_FALHA_IA = (
    "Estou com uma instabilidade para entender mensagens agora. "
    "Tente novamente em alguns instantes, por favor."
)
MENSAGEM_ERRO_INTERNO = "Ops! Tive um problema ao processar sua mensagem. Tente novamente em instantes."

# --- Helpers de periodo ---

LABELS_PERIODO = {
    'dia':    'de Hoje',
    'semana': 'desta Semana',
    'mes':    'deste Mes',
    'ano':    'deste Ano',
}


def _normalizar_quantidade(quantidade) -> int:
    try:
        return max(1, int(quantidade or 1))
    except (TypeError, ValueError):
        return 1


def _data_inicio_periodo(periodo: str, quantidade: int = 1) -> date:
    hoje = timezone.localdate()
    qtd = _normalizar_quantidade(quantidade)
    if periodo == 'dia':
        return hoje - timedelta(days=qtd - 1)
    if periodo == 'semana':
        return hoje - timedelta(days=7 * qtd)
    if periodo == 'ano':
        return hoje.replace(year=hoje.year - (qtd - 1), month=1, day=1)
    # Padrao: 'mes'
    total_meses = (hoje.year * 12 + (hoje.month - 1)) - (qtd - 1)
    ano_inicio = total_meses // 12
    mes_inicio = (total_meses % 12) + 1
    return date(ano_inicio, mes_inicio, 1)


def _label_periodo(periodo: str, quantidade: int = 1) -> str:
    qtd = _normalizar_quantidade(quantidade)
    if qtd <= 1:
        return LABELS_PERIODO.get(periodo, 'deste Mes')
    labels_plural = {
        'dia':    f'dos ultimos {qtd} dias',
        'semana': f'das ultimas {qtd} semanas',
        'mes':    f'dos ultimos {qtd} meses',
        'ano':    f'dos ultimos {qtd} anos',
    }
    return labels_plural.get(periodo, f'dos ultimos {qtd} meses')


def _verificar_assinatura_meta(request) -> bool:
    """
    Valida o cabecalho X-Hub-Signature-256 enviado pela Meta usando WHATSAPP_APP_SECRET.
    Em modo DEBUG sem WHATSAPP_APP_SECRET definido, permite requisicoes locais (ex: Insomnia).
    Em producao (DEBUG=False), WHATSAPP_APP_SECRET e obrigatorio.
    """
    segredo = getattr(settings, 'WHATSAPP_APP_SECRET', '') or ''
    if not segredo:
        if settings.DEBUG:
            return True
        logger.error("WHATSAPP_APP_SECRET nao configurado com DEBUG=False.")
        return False

    assinatura = (
        request.headers.get('X-Hub-Signature-256')
        or request.META.get('HTTP_X_HUB_SIGNATURE_256', '')
    )
    if not assinatura or not assinatura.startswith('sha256='):
        return False

    esperada = 'sha256=' + hmac.new(
        segredo.encode('utf-8'),
        request.body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(assinatura, esperada)


def _analisar(texto_recebido: str) -> dict:
    """Interpreta a mensagem: !mock (so em DEBUG) ou Gemini."""
    if texto_recebido.startswith('!mock '):
        if settings.DEBUG:
            print("MODO MOCK ATIVADO: Ignorando Gemini e lendo JSON direto da mensagem.")
            dados = json.loads(texto_recebido.replace('!mock ', '', 1))
        else:
            logger.warning("Tentativa de uso de !mock bloqueada com DEBUG=False.")
            return {'tipo': 'erro', 'mensagem': 'Modo mock desativado em producao.', 'silencioso': True}
    else:
        print("Enviando para o Gemini analisar...")
        dados = analisar_mensagem(texto_recebido)

    if not isinstance(dados, dict):
        return {'tipo': 'erro', 'mensagem': 'Resposta de IA em formato inesperado.', 'falha_tecnica': True}
    return dados


def _processar_mensagem(telefone_cliente: str, texto_recebido: str) -> None:
    """Interpreta a mensagem, executa a intencao e responde ao usuario pelo WhatsApp."""
    print("\n" + "="*40)
    print(f"Telefone: {telefone_cliente}")
    print(f"Mensagem: {texto_recebido}")
    dados_estruturados = _analisar(texto_recebido)
    print(f"Resultado processado: {json.dumps(dados_estruturados, indent=2, ensure_ascii=False)}")
    print("="*40 + "\n")

    if dados_estruturados.get('tipo') == 'erro':
        if dados_estruturados.get('silencioso'):
            return
        if dados_estruturados.get('falha_tecnica'):
            enviar_mensagem_whatsapp(telefone_cliente, MENSAGEM_FALHA_IA)
        else:
            print("A mensagem nao era financeira. Enviando menu de ajuda.")
            enviar_mensagem_whatsapp(telefone_cliente, MENSAGEM_AJUDA)
        return

    usuario_atual, criado = Usuario.objects.get_or_create(
        telefone=telefone_cliente,
        defaults={'orcamento_mensal': 0.00}
    )

    if criado:
        print(f'Novo usuario cadastrado: {telefone_cliente}')
        texto_boas_vindas = (
            "Ola! Sou o *Kofrim*, seu novo assistente financeiro inteligente!\n\n"
            "Vi que e a sua primeira vez por aqui.\n\n"
            "Comigo, voce nao precisa preencher planilhas chatas. Basta mandar seus gastos de forma natural!\n\n"
            "Exemplos:\n"
            "_'Gastei 45 de pizza'_\n"
            "_'20 reais de uber'_\n"
            "_'Recebi 1500 de salario'_\n\n"
            "Vou processar essa sua primeira mensagem agora mesmo!"
        )
        enviar_mensagem_whatsapp(telefone_cliente, texto_boas_vindas)

    tipo = dados_estruturados['tipo']

    if tipo == 'consulta':
        print("Usuario solicitou resumo.")
        total_receitas = Transacao.objects.filter(usuario=usuario_atual, tipo='receita').aggregate(Sum('valor'))['valor__sum'] or 0
        total_despesas = Transacao.objects.filter(usuario=usuario_atual, tipo='despesa').aggregate(Sum('valor'))['valor__sum'] or 0
        saldo = total_receitas - total_despesas
        texto_resumo = (
            "Resumo do seu Kofrim\n\n"
            f"Entradas: {formatar_valor(total_receitas)}\n"
            f"Saidas: {formatar_valor(total_despesas)}\n\n"
            f"Saldo Atual: {formatar_valor(saldo)}"
        )
        enviar_mensagem_whatsapp(telefone_cliente, texto_resumo)

    elif tipo == 'extrato':
        subtipo = dados_estruturados.get('subtipo', 'ambos')
        periodo = dados_estruturados.get('periodo', 'mes')
        quantidade = _normalizar_quantidade(dados_estruturados.get('quantidade', 1))
        data_inicio = _data_inicio_periodo(periodo, quantidade)
        label_periodo = _label_periodo(periodo, quantidade)

        print(f"Extrato solicitado: subtipo={subtipo} | periodo={periodo} | qtd={quantidade} | de {data_inicio}")

        filtro_base = {'usuario': usuario_atual, 'data__gte': data_inicio}

        if subtipo == 'despesas':
            tipos_buscar = ['despesa']
            titulo = f'Extrato de Despesas {label_periodo}'
        elif subtipo == 'receitas':
            tipos_buscar = ['receita']
            titulo = f'Extrato de Receitas {label_periodo}'
        else:
            tipos_buscar = ['despesa', 'receita']
            titulo = f'Extrato Completo {label_periodo}'

        transacoes = Transacao.objects.filter(**filtro_base, tipo__in=tipos_buscar).order_by('-data', '-id')

        if not transacoes.exists():
            texto_extrato = f"*{titulo}*\n\nNenhuma transacao encontrada neste periodo."
        else:
            linhas = []
            for t in transacoes:
                icone = '-' if t.tipo == 'despesa' else '+'
                data_fmt = t.data.strftime('%d/%m')
                linhas.append(
                    f"{icone} {data_fmt} | {t.categoria.capitalize()} | "
                    f"{formatar_valor(t.valor)} - {t.descricao} (#{t.id})"
                )
            total = transacoes.aggregate(Sum('valor'))['valor__sum'] or 0
            texto_extrato = (
                f"*{titulo}*\n\n"
                + "\n".join(linhas)
                + f"\n\nTotal: {formatar_valor(total)}"
            )
        enviar_mensagem_whatsapp(telefone_cliente, texto_extrato)

    elif tipo == 'exclusao':
        id_alvo = parse_id(dados_estruturados.get('id_referencia'))
        if id_alvo is None:
            enviar_mensagem_whatsapp(telefone_cliente, "Nao entendi qual transacao apagar. Informe o numero (ID) do registro.")
        else:
            try:
                transacao = Transacao.objects.get(id=id_alvo, usuario=usuario_atual)
                transacao.delete()
                enviar_mensagem_whatsapp(telefone_cliente, f"Tudo certo! A transacao #{id_alvo} foi apagada.")
            except Transacao.DoesNotExist:
                enviar_mensagem_whatsapp(telefone_cliente, f"Nao encontrei a transacao #{id_alvo}. Ela ja pode ter sido apagada ou o ID esta errado.")

    elif tipo == 'edicao':
        id_alvo = parse_id(dados_estruturados.get('id_referencia'))
        if id_alvo is None:
            enviar_mensagem_whatsapp(telefone_cliente, "Nao entendi qual transacao editar. Informe o numero (ID) do registro.")
        else:
            try:
                transacao = Transacao.objects.get(id=id_alvo, usuario=usuario_atual)
                if dados_estruturados.get('valor') is not None:
                    novo_valor = parse_valor(dados_estruturados['valor'])
                    if novo_valor is None:
                        enviar_mensagem_whatsapp(telefone_cliente, "Nao consegui entender o novo valor. Tente: 'Muda o valor do registro 3 para 90'.")
                        return
                    transacao.valor = novo_valor
                if dados_estruturados.get('categoria'):
                    transacao.categoria = normalizar_categoria(dados_estruturados['categoria'])
                if dados_estruturados.get('descricao'):
                    transacao.descricao = str(dados_estruturados['descricao']).strip()[:200]
                transacao.save()
                texto_resposta = (
                    f"Transacao #{id_alvo} atualizada com sucesso!\n\n"
                    f"Novo valor: {formatar_valor(transacao.valor)}\n"
                    f"Categoria: {transacao.categoria.capitalize()}"
                )
                enviar_mensagem_whatsapp(telefone_cliente, texto_resposta)
            except Transacao.DoesNotExist:
                enviar_mensagem_whatsapp(telefone_cliente, f"Nao encontrei a transacao #{id_alvo} para editar.")

    elif tipo == 'criar_meta':
        titulo_meta = str(dados_estruturados.get('titulo') or 'Minha Meta').strip()[:100]
        valor_alvo  = parse_valor(dados_estruturados.get('valor_alvo'))
        data_limite = parse_data(dados_estruturados.get('data_limite'))
        if valor_alvo is None:
            enviar_mensagem_whatsapp(telefone_cliente, "Nao entendi o valor da meta. Tente: 'Quero juntar R$ 5.000 ate dezembro'")
        elif data_limite and data_limite <= timezone.localdate():
            enviar_mensagem_whatsapp(telefone_cliente, "O prazo da meta precisa ser uma data futura. Tente novamente com outra data.")
        else:
            _, texto_meta = meta_service.criar_meta(usuario_atual, titulo_meta, valor_alvo, data_limite)
            enviar_mensagem_whatsapp(telefone_cliente, texto_meta)

    elif tipo == 'contribuir_meta':
        valor = parse_valor(dados_estruturados.get('valor'))
        if valor is None:
            enviar_mensagem_whatsapp(telefone_cliente, "Nao entendi o valor da contribuicao. Tente: 'Guardei 500 para minha meta'")
        else:
            texto_contribuicao = meta_service.contribuir_para_meta(usuario_atual, valor)
            enviar_mensagem_whatsapp(telefone_cliente, texto_contribuicao)

    elif tipo == 'consultar_meta':
        texto_status = meta_service.consultar_status_meta(usuario_atual)
        enviar_mensagem_whatsapp(telefone_cliente, texto_status)

    elif tipo == 'definir_orcamento':
        valor_orcamento = parse_valor(dados_estruturados.get('valor'))
        if valor_orcamento is None:
            enviar_mensagem_whatsapp(telefone_cliente, "Nao entendi o valor do orcamento. Tente: 'Meu orcamento mensal e 5000'")
        else:
            usuario_atual.orcamento_mensal = valor_orcamento
            usuario_atual.save(update_fields=['orcamento_mensal'])
            texto_orcamento = f"Orcamento mensal definido: *{formatar_valor(valor_orcamento)}*."
            limite = meta_service.calcular_limite_diario(usuario_atual)
            if limite:
                texto_orcamento += (
                    f"\n\nCom a sua meta ativa, seu limite de gasto por dia e de "
                    f"*{formatar_valor(limite['limite_diario'])}*."
                )
            enviar_mensagem_whatsapp(telefone_cliente, texto_orcamento)

    else:
        # despesa ou receita
        dados_limpos, erro_validacao = validar_transacao(dados_estruturados)
        if erro_validacao:
            enviar_mensagem_whatsapp(telefone_cliente, erro_validacao)
            return

        kwargs_transacao = dict(
            usuario=usuario_atual,
            tipo=dados_limpos['tipo'],
            valor=dados_limpos['valor'],
            categoria=dados_limpos['categoria'],
            descricao=dados_limpos['descricao'],
        )
        if dados_limpos['data']:
            kwargs_transacao['data'] = dados_limpos['data']

        nova_transacao = Transacao.objects.create(**kwargs_transacao)
        print(f"Transacao salva! ID: {nova_transacao.id} | Data: {nova_transacao.data.strftime('%d/%m/%Y')}")

        valor_formatado     = formatar_valor(nova_transacao.valor)
        categoria_formatada = nova_transacao.categoria.capitalize()
        data_formatada      = nova_transacao.data.strftime('%d/%m/%Y')

        texto_resposta = (
            f"Tudo certo!\n\n"
            f"Registrei uma *{nova_transacao.tipo}* de {valor_formatado} "
            f"na categoria *{categoria_formatada}*.\n"
            f"Data: {data_formatada}\n"
            f"(ID: {nova_transacao.id})"
        )

        if nova_transacao.tipo == 'despesa':
            aviso_meta = meta_service.texto_aviso_limite_diario(usuario_atual)
            if aviso_meta:
                texto_resposta += aviso_meta

        enviar_mensagem_whatsapp(telefone_cliente, texto_resposta)


def _processar_mensagem_seguro(telefone_cliente: str, texto_recebido: str) -> str:
    """Executa o processamento sem deixar excecoes escaparem. Retorna o status resultante."""
    try:
        _processar_mensagem(telefone_cliente, texto_recebido)
        return 'recebido'
    except Exception:
        logger.exception("Erro inesperado ao processar mensagem do WhatsApp")
        try:
            enviar_mensagem_whatsapp(telefone_cliente, MENSAGEM_ERRO_INTERNO)
        except Exception:
            logger.exception("Falha ao enviar mensagem de erro ao usuario")
        return 'erro_interno'


def _processar_em_segundo_plano(telefone_cliente: str, texto_recebido: str) -> None:
    try:
        _processar_mensagem_seguro(telefone_cliente, texto_recebido)
    finally:
        connection.close()  # cada thread tem sua propria conexao com o banco


@csrf_exempt
def whatsapp_webhook(request):
    if request.method == 'GET':
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')

        if mode is not None or token is not None:
            verify_token = getattr(settings, 'WHATSAPP_VERIFY_TOKEN', '') or ''
            if (
                mode == 'subscribe'
                and verify_token
                and hmac.compare_digest(token or '', verify_token)
            ):
                return HttpResponse(challenge or '', content_type='text/plain', status=200)
            logger.warning("Falha na verificacao GET do webhook da Meta.")
            return JsonResponse({'erro': 'Token de verificacao invalido'}, status=403)

        return JsonResponse({'status': 'Aguardando verificacao do Meta.'}, status=200)

    elif request.method == 'POST':
        if not _verificar_assinatura_meta(request):
            logger.warning("Requisicao POST com assinatura X-Hub-Signature-256 invalida.")
            return JsonResponse({'erro': 'Assinatura invalida'}, status=403)

        telefone_cliente = None
        try:
            dados = json.loads(request.body)
            mensagem_info = dados['entry'][0]['changes'][0]['value']['messages'][0]

            telefone_cliente = mensagem_info['from']
            message_id = str(mensagem_info.get('id') or '').strip()

            if message_id:
                if MensagemProcessada.objects.filter(message_id=message_id).exists():
                    logger.info("Mensagem duplicada ignorada: %s", message_id)
                    return JsonResponse({'status': 'duplicado'}, status=200)
                try:
                    MensagemProcessada.objects.create(
                        message_id=message_id,
                        telefone=telefone_cliente[:20],
                    )
                except IntegrityError:
                    logger.info("Mensagem duplicada ignorada (concorrencia): %s", message_id)
                    return JsonResponse({'status': 'duplicado'}, status=200)

            tipo_mensagem = mensagem_info.get('type')
            if tipo_mensagem and tipo_mensagem != 'text':
                print(f"Mensagem do tipo '{tipo_mensagem}' recebida de {telefone_cliente}. Avisando que so texto e suportado.")
                enviar_mensagem_whatsapp(telefone_cliente, MENSAGEM_SO_TEXTO)
                return JsonResponse({'status': 'tipo_nao_suportado'}, status=200)

            texto_recebido = mensagem_info['text']['body']

            if getattr(settings, 'WEBHOOK_ASSINCRONO', False):
                threading.Thread(
                    target=_processar_em_segundo_plano,
                    args=(telefone_cliente, texto_recebido),
                    daemon=True,
                ).start()
                return JsonResponse({'status': 'recebido'}, status=200)

            status = _processar_mensagem_seguro(telefone_cliente, texto_recebido)
            print("-" * 40 + "\n")
            return JsonResponse({'status': status}, status=200)

        except KeyError:
            print("Notificacao de status recebida e ignorada.")
            return JsonResponse({'status': 'ignorado'}, status=200)

        except json.JSONDecodeError:
            logger.warning("Payload invalido recebido no webhook.")
            return JsonResponse({'erro': 'Payload invalido'}, status=400)

        except Exception:
            logger.exception("Erro inesperado no webhook do WhatsApp")
            return JsonResponse({'status': 'erro_interno'}, status=200)

    return JsonResponse({'erro': 'Metodo nao permitido'}, status=405)
