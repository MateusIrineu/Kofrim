# 🔐 Kofrim

**Seu Assistente Financeiro Inteligente integrado ao WhatsApp.**

O Kofrim é um sistema de gestão financeira que funciona de forma invisível e fluida. Em vez de preencher formulários complexos em aplicativos, você simplesmente manda uma mensagem de texto (como se falasse com um amigo) e a Inteligência Artificial (Google Gemini) se encarrega de interpretar, categorizar e salvar tudo no seu banco de dados.

---

## ✨ O que o Kofrim faz?

### 💬 Linguagem Natural
Você não precisa seguir comandos rígidos. O sistema entende o que você quer dizer:
- *"Gastei 50 no Ifood"*
- *"Recebi 1500 de um freela"*
- *"Padaria 15 reais ontem"*

### 💸 Gestão de Transações e Categorização
A Inteligência Artificial lê sua mensagem e automaticamente aloca seus gastos em 6 eixos principais: 
`Alimentação`, `Saúde`, `Serviços`, `Transporte`, `Lazer` e `Outros`.
- **Identificação de datas retroativas:** Se você mencionar *"dia 10"* ou *"mês passado"*, ele registra o gasto no dia exato em que ocorreu, mantendo seu histórico impecável.

### 🎯 Metas Financeiras e Rastreador Diário
O Kofrim te ajuda a juntar dinheiro e acompanha seu progresso em tempo real.
- **Criação de Metas:** Defina um objetivo (*"Quero juntar 3 mil para uma viagem até dezembro"*).
- **Rastreador de Orçamento:** Ao estabelecer um prazo para sua meta e seu orçamento mensal, o Kofrim calcula **exatamente quanto você pode gastar por dia** para não estourar.
- **Feedback Constante:** Se a meta estiver ativa, toda vez que você registrar uma despesa, ele te avisa: 
  - ✅ *"Você ainda pode gastar R$ X hoje."* ou 
  - 🚨 *"Você ultrapassou seu limite diário!"*
- **Acompanhamento Visual:** Informe quando guardar dinheiro (*"Guardei 100 na minha meta"*) e receba uma barra de progresso visual de quanto falta para atingir seu alvo.

### 📊 Consultas e Relatórios
Peça relatórios rápidos de forma natural:
- **Resumo de Saldo:** *"Qual meu saldo atual?"*
- **Extrato Inteligente:** *"Mostre os gastos dos últimos 2 meses"*, ou *"O que entrou essa semana?"*. Ele filtra por despesa/receita e pelos períodos exatos que você pedir.

### ✏️ Controle Total do Banco
Errou algum registro? É só pedir:
- *"Apaga o registro número 5"*
- *"Altera o valor do registro 3 para 90 reais"*

---

## ⚙️ Como Funciona por Baixo dos Panos

1. **Recepção (Webhook):** Você envia a mensagem pelo WhatsApp e o webhook do Kofrim (construído em Python/Django) a recebe.
2. **Cérebro (IA):** A mensagem passa pelo **Google Gemini 3.8 Flash**, que foi treinado com um prompt específico para extrair a intenção, os valores, datas e categorizar a ação em um formato JSON estruturado.
3. **Persistência:** O Django processa esse JSON, realiza os cálculos necessários (como orçamentos e limites diários de metas) e salva tudo de forma segura em um banco de dados **PostgreSQL**.
4. **Resposta:** O sistema devolve uma mensagem limpa e formatada confirmando a operação de volta para o seu WhatsApp.

*(O cadastro de usuários é 100% automático. Ao enviar sua primeira mensagem financeira, seu número é reconhecido e seu cofre pessoal é criado instantaneamente).*

---

## 🛠️ Tecnologias Utilizadas

- **Backend:** Python + Django
- **Banco de Dados:** PostgreSQL (Persistência segura e confiável)
- **Inteligência Artificial:** Google Gemini API (`google-genai`)
- **Integração:** Webhooks nativos da API do WhatsApp Cloud (Meta)

---

## 👨‍💻 Para Desenvolvedores (Modo Mock)
O sistema conta com um recurso de bypass da Inteligência Artificial projetado para testes locais ou economia de cota de API.
Se você iniciar o body de sua mensagem via Insomnia/Postman com a tag `!mock ` seguida de um JSON formatado, o servidor ignora a comunicação externa com o Gemini e processa a requisição usando seus dados simulados:

```json
!mock {"tipo": "despesa", "valor": 10, "categoria": "alimentacao", "descricao": "Cafe", "data": "2026-10-02"}
```

