import google.generativeai as genai
import json
from django.conf import settings

genai.configure(api_key=settings.GEMINI_API_KEY)

configuracao = genai.GenerationConfig(
    temperature=0.0, # apenas responde o básico
    response_mime_type="application/json", # retorna respostas em json
)

instrucao_sistema = """
Você é um assistente financeiro. Extraia os dados da mensagem do usuário e retorne um JSON.

Regras para o campo "tipo":
1. Se for um gasto novo, use "despesa".
2. Se for um ganho novo, use "receita".
3. SE O USUÁRIO FIZER PERGUNTA, use "consulta".
4. SE O USUÁRIO PEDIR PARA APAGAR/CANCELAR UM REGISTRO, use "exclusao".
5. SE O USUÁRIO PEDIR PARA ALTERAR/EDITAR UM REGISTRO, use "edicao".
6. Se for algo não financeiro, use "erro".

MUITO IMPORTANTE: 
- Se o tipo for "exclusao" ou "edicao", você DEVE criar uma chave chamada "id_referencia" contendo o número do ID que o usuário quer alterar.
- Na "edicao", retorne também os campos (valor, categoria, descricao) com as novas informações que devem ser atualizadas. Os campos que ele não pedir para alterar podem vir como null.

Categorias permitidas para despesa: alimentacao, transporte, moradia, saude, lazer, assinatura, outros.
"""

modelo = genai.GenerativeModel(
    model_name='gemini-3.6-flash',
    generation_config=configuracao,
    system_instruction=instrucao_sistema
)

def analisar_mensagem(texto_usuario):
    try:
        resposta = modelo.generate_content(texto_usuario)
        dados_extraidos = json.loads(resposta.text)
        return dados_extraidos
    except Exception as e:
        print(f'Erro no Gemini: {e}')
        return {'tipo': 'erro', 'mensagem': 'Falha na extração de dados.'}