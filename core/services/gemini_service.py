import google.generativeai as genai
import json
from django.conf import settings

genai.configure(api_key=settings.GEMINI_API_KEY)

configuracao = genai.GenerationConfig(
    temperature=0.0, # apenas responde o básico
    response_mime_type="application/json", # retorna respostas em json
)

instrucao_sistema = """
Você é o assistente financeiro Kofrim. 
O usuário vai te mandar uma mensagem de texto. Sua única tarefa é extrair os dados financeiros dessa mensagem.
Nunca converse com o usuário. Devolva estritamente um objeto JSON com as seguintes chaves:
- "tipo": "despesa" ou "receita"
- "valor": número decimal (float) positivo
- "categoria": uma palavra curta (ex: "alimentacao", "transporte", "salario")
- "descricao": resumo curto do que foi (ex: "lanche no ifood")
Se não for uma mensagem financeira, devolva "tipo": "erro".
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