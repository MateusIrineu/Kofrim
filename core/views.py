import json
from datetime import date, timedelta, datetime
from django.db.models import Sum
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

#core
from core.services.gemini_service import analisar_mensagem
from core.models import Usuario, Transacao
from core.services.formatacao_service import formatar_valor
from core.services.whatsapp_service import enviar_mensagem_whatsapp
from core.services import meta_service

# --- Helpers de periodo ---

LABELS_PERIODO = {
    'dia':    'de Hoje',
    'semana': 'desta Semana',
    'mes':    'deste Mes',
    'ano':    'deste Ano',
}

def _data_inicio_periodo(periodo: str) -> date:
    hoje = date.today()
    if periodo == 'dia':
        return hoje
    if periodo == 'semana':
        return hoje - timedelta(days=7)
    if periodo == 'ano':
        return hoje.replace(month=1, day=1)
    return hoje.replace(day=1)

@csrf_exempt
def whatsapp_webhook(request):
    if request.method == 'GET':
        return JsonResponse({'status': 'Aguardando verificacao do Meta.'})

    elif request.method == 'POST':
        try:
            dados = json.loads(request.body)
            mensagem_info = dados['entry'][0]['changes'][0]['value']['messages'][0]

            telefone_cliente = mensagem_info['from']
            texto_recebido = mensagem_info['text']['body']

            print("\n" + "="*40)
            print(f"Telefone: {telefone_cliente}")
            print(f"Mensagem: {texto_recebido}")
            print("Enviando para o Gemini analisar...")
            dados_estruturados = analisar_mensagem(texto_recebido)
            print(f"Resultado do Gemini: {json.dumps(dados_estruturados, indent=2, ensure_ascii=False)}")
            print("="*40 + "\n")

            if dados_estruturados.get('tipo') != 'erro':
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
                    data_inicio = _data_inicio_periodo(periodo)
                    label_periodo = LABELS_PERIODO.get(periodo, 'deste Mes')

                    print(f"Extrato solicitado: subtipo={subtipo} | periodo={periodo} | de {data_inicio}")

                    filtro_base = {'usuario': usuario_atual, 'data__gte': data_inicio}

                    if subtipo == 'despesas':
                        tipos_buscar = ['despesa']
                        icone_titulo = '-'
                        titulo = f'Extrato de Despesas {label_periodo}'
                    elif subtipo == 'receitas':
                        tipos_buscar = ['receita']
                        icone_titulo = '+'
                        titulo = f'Extrato de Receitas {label_periodo}'
                    else:
                        tipos_buscar = ['despesa', 'receita']
                        icone_titulo = '='
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
                    id_alvo = dados_estruturados.get('id_referencia')
                    try:
                        transacao = Transacao.objects.get(id=id_alvo, usuario=usuario_atual)
                        transacao.delete()
                        enviar_mensagem_whatsapp(telefone_cliente, f"Tudo certo! A transacao #{id_alvo} foi apagada.")
                    except Transacao.DoesNotExist:
                        enviar_mensagem_whatsapp(telefone_cliente, f"Nao encontrei a transacao #{id_alvo}. Ela ja pode ter sido apagada ou o ID esta errado.")

                elif tipo == 'edicao':
                    id_alvo = dados_estruturados.get('id_referencia')
                    try:
                        transacao = Transacao.objects.get(id=id_alvo, usuario=usuario_atual)
                        if dados_estruturados.get('valor'):
                            transacao.valor = dados_estruturados['valor']
                        if dados_estruturados.get('categoria'):
                            transacao.categoria = dados_estruturados['categoria']
                        if dados_estruturados.get('descricao'):
                            transacao.descricao = dados_estruturados['descricao']
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
                    titulo_meta = dados_estruturados.get('titulo', 'Minha Meta')
                    valor_alvo  = dados_estruturados.get('valor_alvo')
                    data_str    = dados_estruturados.get('data_limite')
                    data_limite = None
                    if data_str:
                        try:
                            data_limite = datetime.strptime(data_str, '%Y-%m-%d').date()
                        except ValueError:
                            pass
                    if not valor_alvo:
                        enviar_mensagem_whatsapp(telefone_cliente, "Nao entendi o valor da meta. Tente: 'Quero juntar R$ 5.000 ate dezembro'")
                    else:
                        _, texto_meta = meta_service.criar_meta(usuario_atual, titulo_meta, valor_alvo, data_limite)
                        enviar_mensagem_whatsapp(telefone_cliente, texto_meta)

                elif tipo == 'contribuir_meta':
                    valor = dados_estruturados.get('valor')
                    if not valor:
                        enviar_mensagem_whatsapp(telefone_cliente, "Nao entendi o valor da contribuicao. Tente: 'Guardei 500 para minha meta'")
                    else:
                        texto_contribuicao = meta_service.contribuir_para_meta(usuario_atual, valor)
                        enviar_mensagem_whatsapp(telefone_cliente, texto_contribuicao)

                elif tipo == 'consultar_meta':
                    texto_status = meta_service.consultar_status_meta(usuario_atual)
                    enviar_mensagem_whatsapp(telefone_cliente, texto_status)

                else:
                    # despesa ou receita
                    data_str = dados_estruturados.get('data')
                    data_transacao = None
                    if data_str:
                        try:
                            data_transacao = datetime.strptime(data_str, '%Y-%m-%d').date()
                        except ValueError:
                            pass

                    kwargs_transacao = dict(
                        usuario=usuario_atual,
                        tipo=dados_estruturados['tipo'],
                        valor=dados_estruturados['valor'],
                        categoria=dados_estruturados['categoria'],
                        descricao=dados_estruturados['descricao'],
                    )
                    if data_transacao:
                        kwargs_transacao['data'] = data_transacao

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

            else:
                print("A mensagem nao era financeira. Nada salvo.")

            print("-" * 40 + "\n")
            return JsonResponse({'status': 'recebido'}, status=200)

        except KeyError:
            print("Notificacao de status recebida e ignorada.")
            return JsonResponse({'status': 'ignorado'}, status=200)

        except Exception as e:
            print(f"Erro inesperado: {e}")
            return JsonResponse({'erro': str(e)}, status=400)

    return JsonResponse({'erro': 'Metodo nao permitido'}, status=405)
