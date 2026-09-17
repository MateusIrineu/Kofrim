import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from core.services.gemini_service import analisar_mensagem
from core.models import Usuario, Transacao

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

                nova_transacao = Transacao.objects.create(
                    usuario=usuario_atual,
                    tipo=dados_estruturados['tipo'],
                    valor=dados_estruturados['valor'],
                    categoria=dados_estruturados['categoria'],
                    descricao=dados_estruturados['descricao'],
                )
                print(f"💾 Transação salva no banco! ID: {nova_transacao.id}")

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