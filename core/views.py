import json
from django.db.models import Sum
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

#core
from core.services.gemini_service import analisar_mensagem
from core.models import Usuario, Transacao
from core.services.formatacao_service import formatar_valor
from core.services.whatsapp_service import enviar_mensagem_whatsapp

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
            print(f"📞 Telefone: {telefone_cliente}")
            print(f"💬 Mensagem: {texto_recebido}")
            print("🧠 Enviando para o Gemini analisar...")
            dados_estruturados = analisar_mensagem(texto_recebido)
            print(f"✅ Resultado do Gemini: {json.dumps(dados_estruturados, indent=2)}")
            print("="*40 + "\n")

            if dados_estruturados.get('tipo') != 'erro':
                usuario_atual, criado = Usuario.objects.get_or_create(
                    telefone=telefone_cliente,
                    defaults={'orcamento_mensal': 0.00}
                )

                if criado:
                    print(f'👤 Novo usuário cadastrado automaticamente: {telefone_cliente}')

                    texto_boas_vindas = (
                    "👋 Olá! Sou o *Kofrim*, seu novo assistente financeiro inteligente!\n\n"
                    "Vi que é a sua primeira vez por aqui. 🎉\n\n"
                    "Comigo, você não precisa preencher planilhas chatas. Basta mandar seus gastos de forma natural, como se falasse com um amigo!\n\n"
                    "💡 *Exemplos:*\n"
                    "🍕 _'Gastei 45 de pizza'_\n"
                    "🚗 _'20 reais de uber'_\n"
                    "💰 _'Recebi 1500 de salário'_\n\n"
                    "Vou processar essa sua primeira mensagem agora mesmo! 👇"
                )
                    enviar_mensagem_whatsapp(telefone_cliente, texto_boas_vindas)

                if dados_estruturados['tipo'] == 'consulta':
                    print("📊 Usuário solicitou resumo. Calculando...")

                    total_receitas = Transacao.objects.filter(usuario=usuario_atual, tipo='receita'). aggregate(Sum('valor'))['valor__sum'] or 0
                    total_despesas = Transacao.objects.filter(usuario=usuario_atual, tipo='despesa'). aggregate(Sum('valor'))['valor__sum'] or 0
                    saldo = total_receitas - total_despesas

                    texto_resumo = (
                        "📊 *Resumo do seu Kofrim*\n\n"
                        f"📈 Entradas: {formatar_valor(total_receitas)}\n"
                        f"📉 Saídas: {formatar_valor(total_despesas)}\n\n"
                        f"💰 *Saldo Atual: {formatar_valor(saldo)}*"
                    )

                    enviar_mensagem_whatsapp(telefone_cliente, texto_resumo)

                elif dados_estruturados['tipo'] == 'exclusao':
                    id_alvo = dados_estruturados.get('id_referencia')
                    try:
                        transacao = Transacao.objects.get(id=id_alvo, usuario=usuario_atual)
                        transacao.delete()

                        enviar_mensagem_whatsapp(telefone_cliente, f"🗑️ Tudo certo! A transação #{id_alvo} foi apagada.")
                    except Transacao.DoesNotExist:
                        enviar_mensagem_whatsapp(telefone_cliente, f"❌ Não encontrei a transação #{id_alvo}. Ela já pode ter sido apagada ou o ID está errado.")

                elif dados_estruturados['tipo'] == 'edicao':
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
                            f"✏️ Transação #{id_alvo} atualizada com sucesso!\n\n"
                            f"Novo valor: {formatar_valor(transacao.valor)}\n"
                            f"Categoria: {transacao.categoria.capitalize()}"
                        )

                        enviar_mensagem_whatsapp(telefone_cliente, texto_resposta)

                    except Transacao.DoesNotExist:
                        enviar_mensagem_whatsapp(telefone_cliente, f"❌ Não encontrei a transação #{id_alvo} para editar.")

                else:
                    nova_transacao = Transacao.objects.create(
                        usuario=usuario_atual,
                        tipo=dados_estruturados['tipo'],
                        valor=dados_estruturados['valor'],
                        categoria=dados_estruturados['categoria'],
                        descricao=dados_estruturados['descricao'],
                    )
                    print(f"💾 Transação salva no banco! ID: {nova_transacao.id}")

                    valor_formatado = formatar_valor(nova_transacao.valor)
                    categoria_formatada = nova_transacao.categoria.capitalize()
                    texto_resposta = f'✅ Tudo certo!\n\nRegistrei uma *{nova_transacao.tipo}* de {valor_formatado} na categoria *{categoria_formatada}*.\n(ID: {nova_transacao.id})'

                    enviar_mensagem_whatsapp(telefone_cliente, texto_resposta)

            else:
                print("❌ A mensagem não era financeira. Nada foi salvo no banco.")

            print("-" * 40 + "\n")


            return JsonResponse({'status': 'recebido'}, status=200)

        except KeyError:
            print("Notificação de status recebida e ignorada.")
            return JsonResponse({'status': 'ignorado'}, status=200)
        
        except Exception as e:
            print(f"Erro inesperado: {e}")
            return JsonResponse({'erro': str(e)}, status=400)

    return JsonResponse({'erro': 'Metodo nao permitido'}, status=405)