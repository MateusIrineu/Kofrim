import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core import views
from core.models import Meta, Transacao, Usuario
from core.services import meta_service
from core.services.formatacao_service import formatar_valor
from core.tests import TELEFONE, OUTRO_TELEFONE, URL, WebhookTestBase


@override_settings(DEBUG=True, WHATSAPP_APP_SECRET='')
class SuiteCompletaInteracoesTests(WebhookTestBase):
    """
    Suíte completa de testes cobrindo todas as interações funcionais do Kofrim:
    - Adições monetárias (receitas)
    - Despesas em todas as 6 categorias
    - Feedback contínuo de limite diário de metas
    - Metas (criar com/sem prazo, contribuir, estourar 100%, consultar)
    - Resumo de saldo (positivo, negativo, zerado)
    - Extrato com todas as variações de subtipo, período e quantidade
    - Edição e Exclusão com isolamento de usuário
    - Definição de orçamento
    - Fluxo de boas-vindas vs mensagens de ajuda
    - Casos extremos / edge cases
    """

    # -------------------------------------------------------------------------
    # 1. ADIÇÃO MONETÁRIA (RECEITAS)
    # -------------------------------------------------------------------------
    def test_receita_sem_data_salva_com_data_de_hoje(self):
        self.post_mock({
            'tipo': 'receita',
            'valor': 2500,
            'categoria': 'outros',
            'descricao': 'Salario Mensal'
        })
        self.assertEqual(Transacao.objects.count(), 1)
        t = Transacao.objects.get()
        self.assertEqual(t.tipo, 'receita')
        self.assertEqual(t.valor, Decimal('2500.00'))
        self.assertEqual(t.data, timezone.localdate())
        self.assertEqual(t.descricao, 'Salario Mensal')
        self.assertIn('R$ 2.500,00', self.ultima_resposta())
        self.assertIn('receita', self.ultima_resposta().lower())

    def test_receita_retroativa_salva_na_data_correta(self):
        data_passada = (timezone.localdate() - timedelta(days=15)).isoformat()
        self.post_mock({
            'tipo': 'receita',
            'valor': 350.50,
            'categoria': 'outros',
            'descricao': 'Venda de bicicleta',
            'data': data_passada
        })
        t = Transacao.objects.get()
        self.assertEqual(t.tipo, 'receita')
        self.assertEqual(t.valor, Decimal('350.50'))
        self.assertEqual(t.data.isoformat(), data_passada)

    # -------------------------------------------------------------------------
    # 2. DESPESAS EM TODAS AS 6 CATEGORIAS
    # -------------------------------------------------------------------------
    def test_despesas_em_todas_as_seis_categorias(self):
        categorias = ['alimentacao', 'saude', 'servicos', 'transporte', 'lazer', 'outros']
        for cat in categorias:
            with self.subTest(categoria=cat):
                self.post_mock({
                    'tipo': 'despesa',
                    'valor': 50,
                    'categoria': cat,
                    'descricao': f'Gasto com {cat}'
                })
        self.assertEqual(Transacao.objects.filter(tipo='despesa').count(), 6)
        salvas = set(Transacao.objects.values_list('categoria', flat=True))
        self.assertEqual(salvas, set(categorias))

    # -------------------------------------------------------------------------
    # 3. METAS E FEEDBACK CONTÍNUO DE LIMITE DIÁRIO
    # -------------------------------------------------------------------------
    def test_despesa_com_meta_ativa_exibe_feedback_limite_diario(self):
        # Orçamento de 3000 (R$ 100/dia base)
        self.usuario.orcamento_mensal = Decimal('3000.00')
        self.usuario.save()

        # Meta de juntar 1000 em 50 dias (economia necessária: 1000/50 = 20/dia)
        # Limite diário disponível = 100 - 20 = 80/dia
        prazo = (timezone.localdate() + timedelta(days=50)).isoformat()
        self.post_mock({
            'tipo': 'criar_meta',
            'titulo': 'Reserva',
            'valor_alvo': 1000,
            'data_limite': prazo
        })

        # Despesa 1: R$ 30 (Abaixo do limite de R$ 80 -> restam R$ 50)
        self.post_mock({
            'tipo': 'despesa',
            'valor': 30,
            'categoria': 'alimentacao',
            'descricao': 'Almoco'
        })
        resp1 = self.ultima_resposta()
        self.assertIn('Meta: Reserva', resp1)
        self.assertIn('voce ainda pode gastar *R$ 50,00* hoje', resp1)

        # Despesa 2: R$ 50 (Atinge exatamente o limite de R$ 80 -> restante 0)
        self.post_mock({
            'tipo': 'despesa',
            'valor': 50,
            'categoria': 'transporte',
            'descricao': 'Uber'
        })
        resp2 = self.ultima_resposta()
        self.assertIn('Você atingiu seu limite exato de gastos para hoje', resp2)

        # Despesa 3: R$ 20 (Estoura o limite em R$ 20)
        self.post_mock({
            'tipo': 'despesa',
            'valor': 20,
            'categoria': 'lazer',
            'descricao': 'Cinema'
        })
        resp3 = self.ultima_resposta()
        self.assertIn('Você ultrapassou seu limite diário em *R$ 20,00*', resp3)

    def test_meta_sem_prazo_definido_acompanha_progresso_sem_limite_diario(self):
        self.post_mock({
            'tipo': 'criar_meta',
            'titulo': 'Fundo de Emergencia',
            'valor_alvo': 5000,
            'data_limite': None
        })
        self.assertIn('Sem data definida', self.ultima_resposta())
        meta = Meta.objects.get(usuario=self.usuario)
        self.assertIsNone(meta.data_limite)

        # Consulta não deve falhar nem exibir limite de gasto para hoje
        self.post_mock({'tipo': 'consultar_meta'})
        self.assertIn('Status da Meta: Fundo de Emergencia', self.ultima_resposta())
        self.assertNotIn('Limite de gasto para hoje', self.ultima_resposta())

    def test_contribuir_para_meta_ate_completar(self):
        self.post_mock({
            'tipo': 'criar_meta',
            'titulo': 'Monitor Novo',
            'valor_alvo': 1000
        })
        self.post_mock({'tipo': 'contribuir_meta', 'valor': 400})
        self.assertIn('40%', self.ultima_resposta())

        # Contribui até ultrapassar
        self.post_mock({'tipo': 'contribuir_meta', 'valor': 700})
        meta = Meta.objects.get(usuario=self.usuario)
        self.assertEqual(meta.valor_atual, Decimal('1100.00'))
        self.assertIn('100%', self.ultima_resposta())
        self.assertIn('Falta: R$ 0,00', self.ultima_resposta())

    def test_consultar_meta_sem_nenhuma_meta_cadastrada(self):
        self.post_mock({'tipo': 'consultar_meta'})
        self.assertIn('Voce nao tem nenhuma meta ativa', self.ultima_resposta())

    # -------------------------------------------------------------------------
    # 4. DEFINIR ORÇAMENTO MENSAL
    # -------------------------------------------------------------------------
    def test_definir_orcamento_recalcula_limite_diario_imediatamente(self):
        prazo = (timezone.localdate() + timedelta(days=30)).isoformat()
        self.post_mock({
            'tipo': 'criar_meta',
            'titulo': 'Celular',
            'valor_alvo': 600,
            'data_limite': prazo
        })
        # Inicialmente orçamento é 5000 (definido no setUp)
        self.post_mock({'tipo': 'definir_orcamento', 'valor': 3000})
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.orcamento_mensal, Decimal('3000.00'))
        self.assertIn('Orcamento mensal definido: *R$ 3.000,00*', self.ultima_resposta())
        self.assertIn('limite de gasto por dia e de *R$ 80,00*', self.ultima_resposta())

    # -------------------------------------------------------------------------
    # 5. RESUMO DE SALDO (CONSULTA)
    # -------------------------------------------------------------------------
    def test_consulta_saldo_positivo(self):
        Transacao.objects.create(usuario=self.usuario, tipo='receita', valor=1000, descricao='Entrada')
        Transacao.objects.create(usuario=self.usuario, tipo='despesa', valor=300, descricao='Saida')
        self.post_mock({'tipo': 'consulta'})
        resp = self.ultima_resposta()
        self.assertIn('Entradas: R$ 1.000,00', resp)
        self.assertIn('Saidas: R$ 300,00', resp)
        self.assertIn('Saldo Atual: R$ 700,00', resp)

    def test_consulta_saldo_negativo(self):
        Transacao.objects.create(usuario=self.usuario, tipo='despesa', valor=500, descricao='Saida')
        self.post_mock({'tipo': 'consulta'})
        resp = self.ultima_resposta()
        self.assertIn('Entradas: R$ 0,00', resp)
        self.assertIn('Saidas: R$ 500,00', resp)
        self.assertIn('Saldo Atual: R$ -500,00', resp)

    def test_consulta_saldo_zerado(self):
        self.post_mock({'tipo': 'consulta'})
        resp = self.ultima_resposta()
        self.assertIn('Entradas: R$ 0,00', resp)
        self.assertIn('Saidas: R$ 0,00', resp)
        self.assertIn('Saldo Atual: R$ 0,00', resp)

    # -------------------------------------------------------------------------
    # 6. EXTRATOS: SUBTIPOS, PERÍODOS E QUANTIDADES
    # -------------------------------------------------------------------------
    def test_extrato_subtipos_e_periodo_vazio(self):
        hoje = timezone.localdate()
        Transacao.objects.create(usuario=self.usuario, tipo='receita', valor=500, categoria='outros', descricao='Freela', data=hoje)
        Transacao.objects.create(usuario=self.usuario, tipo='despesa', valor=100, categoria='alimentacao', descricao='Restaurante', data=hoje)

        # Extrato apenas de receitas
        self.post_mock({'tipo': 'extrato', 'subtipo': 'receitas', 'periodo': 'dia'})
        resp_rec = self.ultima_resposta()
        self.assertIn('Extrato de Receitas', resp_rec)
        self.assertIn('Freela', resp_rec)
        self.assertNotIn('Restaurante', resp_rec)

        # Extrato apenas de despesas
        self.post_mock({'tipo': 'extrato', 'subtipo': 'despesas', 'periodo': 'dia'})
        resp_desp = self.ultima_resposta()
        self.assertIn('Extrato de Despesas', resp_desp)
        self.assertIn('Restaurante', resp_desp)
        self.assertNotIn('Freela', resp_desp)

        # Extrato completo
        self.post_mock({'tipo': 'extrato', 'subtipo': 'ambos', 'periodo': 'dia'})
        resp_ambos = self.ultima_resposta()
        self.assertIn('Extrato Completo', resp_ambos)
        self.assertIn('Freela', resp_ambos)
        self.assertIn('Restaurante', resp_ambos)

    def test_extrato_periodo_sem_transacoes_retorna_aviso_amigavel(self):
        self.post_mock({'tipo': 'extrato', 'subtipo': 'ambos', 'periodo': 'dia'})
        self.assertIn('Nenhuma transacao encontrada neste periodo', self.ultima_resposta())

    # -------------------------------------------------------------------------
    # 7. EDIÇÃO E EXCLUSÃO COM ISOLAMENTO DE USUÁRIO
    # -------------------------------------------------------------------------
    def test_exclusao_e_edicao_bem_sucedida(self):
        t = Transacao.objects.create(
            usuario=self.usuario, tipo='despesa', valor=80,
            categoria='alimentacao', descricao='Jantar'
        )
        # Edição
        self.post_mock({
            'tipo': 'edicao',
            'id_referencia': t.id,
            'valor': 95,
            'categoria': 'lazer',
            'descricao': 'Jantar com amigos'
        })
        t.refresh_from_db()
        self.assertEqual(t.valor, Decimal('95.00'))
        self.assertEqual(t.categoria, 'lazer')
        self.assertEqual(t.descricao, 'Jantar com amigos')

        # Exclusão
        self.post_mock({'tipo': 'exclusao', 'id_referencia': t.id})
        self.assertFalse(Transacao.objects.filter(id=t.id).exists())
        self.assertIn('foi apagada', self.ultima_resposta())

    def test_tentativa_de_manipular_transacao_de_outro_usuario_falha(self):
        outro = Usuario.objects.create(telefone=OUTRO_TELEFONE, orcamento_mensal=0)
        t_alheia = Transacao.objects.create(
            usuario=outro, tipo='despesa', valor=300,
            categoria='saude', descricao='Remedio de outro'
        )
        # Usuário principal tenta editar
        self.post_mock({'tipo': 'edicao', 'id_referencia': t_alheia.id, 'valor': 10})
        self.assertIn('Nao encontrei a transacao', self.ultima_resposta())
        t_alheia.refresh_from_db()
        self.assertEqual(t_alheia.valor, Decimal('300.00'))

        # Usuário principal tenta excluir
        self.post_mock({'tipo': 'exclusao', 'id_referencia': t_alheia.id})
        self.assertIn('Nao encontrei a transacao', self.ultima_resposta())
        self.assertTrue(Transacao.objects.filter(id=t_alheia.id).exists())

    # -------------------------------------------------------------------------
    # 8. CADASTRO AUTOMÁTICO VS MENSAGEM DE AJUDA
    # -------------------------------------------------------------------------
    def test_primeira_mensagem_valida_cadastra_e_manda_boas_vindas(self):
        novo_numero = '5584988776655'
        self.post_mock({
            'tipo': 'despesa',
            'valor': 22,
            'categoria': 'alimentacao',
            'descricao': 'Pastel'
        }, telefone=novo_numero)

        self.assertTrue(Usuario.objects.filter(telefone=novo_numero).exists())
        self.assertIn('Ola! Sou o *Kofrim*', self.enviar.call_args_list[0][0][1])
        self.assertIn('Registrei uma *despesa* de R$ 22,00', self.enviar.call_args_list[1][0][1])

    def test_primeira_mensagem_invalida_nao_cadastra_usuario_e_manda_ajuda(self):
        numero_curioso = '5584911223344'
        with patch('core.views.analisar_mensagem', return_value={'tipo': 'erro', 'mensagem': 'saudacao'}):
            self.post_texto('Ola boa tarde!', telefone=numero_curioso)

        self.assertFalse(Usuario.objects.filter(telefone=numero_curioso).exists())
        self.assertIn('Eu sou o *Kofrim*', self.ultima_resposta())
        self.assertIn('*Registrar*', self.ultima_resposta())
