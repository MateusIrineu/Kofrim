from datetime import timedelta
import json
from google import genai
from google.genai import types
from django.conf import settings
from django.utils import timezone

# Inicializa o cliente com a API key
client = genai.Client(api_key=settings.GEMINI_API_KEY)

instrucao_sistema = """
Voce e um assistente financeiro. Extraia os dados da mensagem do usuario e retorne um JSON.

Regras para o campo "tipo":
1. Se for um gasto novo, use "despesa".
2. Se for um ganho novo, use "receita".
3. SE O USUARIO FIZER PERGUNTA SOBRE SEU SALDO OU RESUMO GERAL, use "consulta".
4. SE O USUARIO PEDIR PARA APAGAR/CANCELAR UM REGISTRO, use "exclusao".
5. SE O USUARIO PEDIR PARA ALTERAR/EDITAR UM REGISTRO, use "edicao".
6. SE O USUARIO QUISER VER UMA LISTA DETALHADA DE TRANSACOES (gastos, entradas, extrato, historico, movimentacoes), use "extrato". Veja as regras abaixo.
7. SE O USUARIO QUISER CRIAR/DEFINIR UMA META FINANCEIRA, use "criar_meta". Veja as regras abaixo.
8. SE O USUARIO QUISER ADICIONAR DINHEIRO A SUA META (guardar, juntar, depositar, contribuir), use "contribuir_meta". Veja as regras abaixo.
9. SE O USUARIO QUISER VER O STATUS/PROGRESSO DA SUA META, use "consultar_meta".
10. SE O USUARIO INFORMAR/DEFINIR/ALTERAR SEU ORCAMENTO OU RENDA MENSAL (ex.: "meu orcamento mensal e 5000", "minha renda livre e 3500"), use "definir_orcamento". Veja as regras abaixo.
11. Se for algo nao financeiro (saudacao como "oi", "bom dia", pedido de ajuda ou qualquer outro assunto), use "erro".

CAMPO "data" (OBRIGATORIO para despesa e receita):
- Se o usuario mencionar uma data especifica, extraia e retorne no formato "YYYY-MM-DD".
- Se nao mencionar data, retorne null (o sistema usara a data de hoje automaticamente).
- Considere a data de hoje informada no contexto da mensagem para calcular datas relativas ("ontem", "dia 10 do mes passado", "dia 5").
- "hoje" ou sem mencao de data -> null

CATEGORIAS PERMITIDAS PARA DESPESA:
- "alimentacao": restaurante, lanchonete, delivery, ifood, rappi, padaria, mercado, supermercado, feira, hortifruti. APENAS comida e bebida.
- "saude": farmacia, remedios, medico, dentista, consulta, exame, fisioterapia, psicologo, terapeuta, hospital, pronto-socorro, vacina, oculos, suplementos, vitaminas, plano de saude.
- "servicos": academia, mensalidade, streaming (netflix, spotify, etc), internet, luz, agua, gas, condominio, celular, assinatura, seguro.
- "transporte": uber, 99, taxi, onibus, metro, trem, combustivel, gasolina, estacionamento, pedagio, manutencao de carro.
- "lazer": cinema, show, festa, viagem, hotel, passeio, jogo, videogame, hobby, livro, presente, bar, balada.
- "outros": qualquer gasto que nao se encaixe acima.

EXEMPLOS DE CLASSIFICACAO:
- "academia 50 reais" -> {"tipo": "despesa", "valor": 50, "categoria": "servicos", "descricao": "Academia", "data": null}
- "lanche 5 reais dia 10 do mes passado" -> {"tipo": "despesa", "valor": 5, "categoria": "alimentacao", "descricao": "Lanche", "data": "2026-09-10"}
- "farmacia 80 ontem" -> {"tipo": "despesa", "valor": 80, "categoria": "saude", "descricao": "Farmacia", "data": "2026-10-01"}
- "recebi 1500 salario dia 1" -> {"tipo": "receita", "valor": 1500, "categoria": "outros", "descricao": "Salario", "data": "2026-10-01"}

REGRAS PARA O TIPO "extrato":
Quando o tipo for "extrato", voce DEVE incluir obrigatoriamente os campos: "subtipo", "periodo" e "quantidade".

Campo "subtipo":
- Use "despesas" se mencionar: gastos, gasto, despesas, despesa, saidas, saida, gastei, compras, pagamentos, o que saiu, o que gastei, quanto gastei.
- Use "receitas" se mencionar: entradas, entrada, receitas, receita, ganhos, ganho, entrou, recebi, o que entrou, renda.
- Use "ambos" se usar termos genericos: extrato, tudo, movimentacoes, historico, registros, transacoes.

Campo "periodo":
- Use "dia" se mencionar: hoje, do dia, diario.
- Use "semana" se mencionar: semana, essa semana, semanal, ultimos 7 dias, da semana, semanais.
- Use "mes" como PADRAO se nao mencionar periodo, ou se mencionar: mes, esse mes, mensal, do mes.
- Use "ano" se mencionar: ano, esse ano, anual, do ano.

Campo "quantidade":
- Extraia o numero de periodos. Use 1 como PADRAO se nao mencionar.
- "ultimos 2 meses" -> quantidade: 2 | "ultimas 3 semanas" -> quantidade: 3 | "esse mes" -> quantidade: 1

REGRAS PARA O TIPO "criar_meta":
- "titulo": nome da meta. Se nao mencionado, use "Minha Meta".
- "valor_alvo": valor numerico (obrigatorio).
- "data_limite": data no formato "YYYY-MM-DD" se o usuario mencionar prazo. Se nao, use null.

REGRAS PARA O TIPO "contribuir_meta":
- "valor": valor numerico a ser adicionado a meta (obrigatorio).

REGRAS PARA O TIPO "definir_orcamento":
- "valor": valor numerico do orcamento mensal (obrigatorio). Ex.: "meu orcamento mensal e 5000" -> {"tipo": "definir_orcamento", "valor": 5000}

MUITO IMPORTANTE:
- Se o tipo for "exclusao" ou "edicao", voce DEVE criar uma chave "id_referencia" com o numero do ID.
- Na "edicao", retorne tambem os campos (valor, categoria, descricao) com as novas informacoes. Campos nao alterados vem como null.
"""


def _montar_configuracao():
    hoje = timezone.localdate()
    ontem = hoje - timedelta(days=1)
    contexto_data = (
        f"\nCONTEXTO DE DATA ATUAL: Hoje e {hoje.isoformat()} "
        f"(ano {hoje.year}, mes {hoje.month:02d}, dia {hoje.day:02d}; ontem foi {ontem.isoformat()}).\n"
    )
    return types.GenerateContentConfig(
        system_instruction=instrucao_sistema + contexto_data,
        temperature=0.0,
        response_mime_type="application/json",
    )


configuracao = _montar_configuracao()


def analisar_mensagem(texto_usuario):
    try:
        resposta = client.models.generate_content(
            model='gemini-3.8-flash',
            contents=texto_usuario,
            config=_montar_configuracao(),
        )
        dados_extraidos = json.loads(resposta.text)
        return dados_extraidos
    except Exception as e:
        print(f'Erro no Gemini: {e}')
        return {'tipo': 'erro', 'mensagem': 'Falha na extracao de dados.', 'falha_tecnica': True}
