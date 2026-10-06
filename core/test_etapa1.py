import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from core.models import Meta, Transacao, Usuario
from core.services.validacao_service import (
    normalizar_categoria, parse_data, parse_id, parse_valor, validar_transacao,
)

TELEFONE = '5511999990000'
OUTRO_TELEFONE = '5511888880000'
URL = '/api/whatsapp/'


class WebhookTestBase(TestCase):
    """Base: envia mensagens pelo webhook usando o modo !mock e captura as respostas."""

    def setUp(self):
        patcher = patch('core.views.enviar_mensagem_whatsapp')
        self.enviar = patcher.start()
        self.addCleanup(patcher.stop)
        self.usuario = Usuario.objects.create(telefone=TELEFONE, orcamento_mensal=5000)

    def post_texto(self, texto, telefone=TELEFONE):
        payload = {'entry': [{'changes': [{'value': {'messages': [
            {'from': telefone, 'text': {'body': texto}}
        ]}}]}]}
        return self.client.post(URL, data=json.dumps(payload), content_type='application/json')

    def post_mock(self, dados, telefone=TELEFONE):
        return self.post_texto('!mock ' + json.dumps(dados), telefone)

    def ultima_resposta(self):
        return self.enviar.call_args[0][1]


class ItemUmDataRetroativaTests(WebhookTestBase):
    def test_data_retroativa_e_gravada(self):
        self.post_mock({'tipo': 'despesa', 'valor': 20, 'categoria': 'servicos',
                        'descricao': 'Netflix', 'data': '2026-09-02'})
        self.assertEqual(Transacao.objects.get().data, date(2026, 9, 2))

    def test_sem_data_usa_hoje(self):
        self.post_mock({'tipo': 'despesa', 'valor': 10, 'categoria': 'outros', 'descricao': 'x'})
        self.assertEqual(Transacao.objects.get().data, timezone.localdate())

    def test_data_invalida_cai_para_hoje(self):
        self.post_mock({'tipo': 'despesa', 'valor': 10, 'categoria': 'outros',
                        'descricao': 'x', 'data': '31/02/2026'})
        self.assertEqual(Transacao.objects.get().data, timezone.localdate())


class ItemDoisCategoriaSaudeTests(WebhookTestBase):
    def test_saude_esta_nas_categorias_do_model(self):
        self.assertIn('saude', [c[0] for c in Transacao.CATEGORIAS])

    def test_transacao_saude_e_gravada(self):
        self.post_mock({'tipo': 'despesa', 'valor': 80, 'categoria': 'saude', 'descricao': 'Farmacia'})
        self.assertEqual(Transacao.objects.get().categoria, 'saude')

    def test_full_clean_aceita_saude(self):
        t = Transacao(usuario=self.usuario, tipo='despesa', valor=10, categoria='saude', descricao='x')
        t.full_clean()


class ItemTresSettingsTests(TestCase):
    def test_fuso_horario_brasil(self):
        self.assertEqual(settings.TIME_ZONE, 'America/Sao_Paulo')

    def test_allowed_hosts_nao_vazio(self):
        self.assertTrue(settings.ALLOWED_HOSTS)

    def test_debug_nao_esta_fixo_no_codigo(self):
        from pathlib import Path
        codigo = (Path(settings.BASE_DIR) / 'setup' / 'settings.py').read_text(encoding='utf-8')
        self.assertNotIn('DEBUG = True', codigo)


class ItemSeteTratamentoDeErrosTests(WebhookTestBase):
    def test_erro_interno_nao_vaza_detalhes_e_retorna_200(self):
        with patch('core.views.Transacao.objects.create', side_effect=RuntimeError('SEGREDO-INTERNO')):
            resp = self.post_mock({'tipo': 'despesa', 'valor': 10, 'categoria': 'outros', 'descricao': 'x'})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('SEGREDO-INTERNO', resp.content.decode())
        self.assertIn('problema', self.ultima_resposta())
        self.assertNotIn('SEGREDO-INTERNO', self.ultima_resposta())

    def test_payload_invalido_retorna_400_sem_detalhes(self):
        resp = self.client.post(URL, data='isto nao e json', content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn('Expecting', resp.content.decode())

    def test_notificacao_de_status_e_ignorada(self):
        payload = {'entry': [{'changes': [{'value': {'statuses': [{'id': 'x'}]}}]}]}
        resp = self.client.post(URL, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'ignorado')


class ItemOitoValidacaoTests(WebhookTestBase):
    def test_valores_invalidos_sao_rejeitados(self):
        for valor in [-5, 0, 'abc', None, '', 10 ** 9, True, 'NaN', 'Infinity']:
            with self.subTest(valor=valor):
                self.post_mock({'tipo': 'despesa', 'valor': valor, 'categoria': 'outros', 'descricao': 'x'})
                self.assertEqual(Transacao.objects.count(), 0, f'valor {valor!r} nao deveria ser salvo')
                self.assertIn('valor', self.ultima_resposta().lower())

    def test_valor_com_virgula_e_aceito(self):
        self.post_mock({'tipo': 'despesa', 'valor': '12,50', 'categoria': 'outros', 'descricao': 'x'})
        self.assertEqual(Transacao.objects.get().valor, Decimal('12.50'))

    def test_categoria_desconhecida_vira_outros(self):
        self.post_mock({'tipo': 'despesa', 'valor': 10, 'categoria': 'inventada', 'descricao': 'x'})
        self.assertEqual(Transacao.objects.get().categoria, 'outros')

    def test_tipo_invalido_nao_salva(self):
        self.post_mock({'tipo': 'transferencia', 'valor': 10, 'categoria': 'outros', 'descricao': 'x'})
        self.assertEqual(Transacao.objects.count(), 0)

    def test_descricao_longa_e_truncada_e_vazia_recebe_padrao(self):
        self.post_mock({'tipo': 'despesa', 'valor': 10, 'categoria': 'outros', 'descricao': 'a' * 500})
        self.post_mock({'tipo': 'despesa', 'valor': 10, 'categoria': 'outros', 'descricao': ''})
        descricoes = sorted(Transacao.objects.values_list('descricao', flat=True), key=len)
        self.assertEqual(descricoes[0], 'Sem descricao')
        self.assertEqual(len(descricoes[1]), 200)

    def test_id_invalido_na_exclusao(self):
        for id_ref in [None, 'abc', -1, 0]:
            with self.subTest(id_ref=id_ref):
                resp = self.post_mock({'tipo': 'exclusao', 'id_referencia': id_ref})
                self.assertEqual(resp.status_code, 200)
                self.assertIn('Nao entendi', self.ultima_resposta())

    def test_nao_apaga_nem_edita_transacao_de_outro_usuario(self):
        outro = Usuario.objects.create(telefone=OUTRO_TELEFONE, orcamento_mensal=0)
        alheia = Transacao.objects.create(usuario=outro, tipo='despesa', valor=50,
                                          categoria='outros', descricao='alheia')
        self.post_mock({'tipo': 'exclusao', 'id_referencia': alheia.id})
        self.post_mock({'tipo': 'edicao', 'id_referencia': alheia.id, 'valor': 1})
        alheia.refresh_from_db()
        self.assertEqual(alheia.valor, Decimal('50.00'))

    def test_edicao_valida_valor_e_categoria(self):
        t = Transacao.objects.create(usuario=self.usuario, tipo='despesa', valor=50,
                                     categoria='outros', descricao='x')
        self.post_mock({'tipo': 'edicao', 'id_referencia': t.id, 'valor': -10})
        t.refresh_from_db()
        self.assertEqual(t.valor, Decimal('50.00'))
        self.post_mock({'tipo': 'edicao', 'id_referencia': t.id, 'valor': 90, 'categoria': 'inventada'})
        t.refresh_from_db()
        self.assertEqual((t.valor, t.categoria), (Decimal('90.00'), 'outros'))

    def test_meta_com_prazo_no_passado_e_rejeitada(self):
        ontem = (timezone.localdate() - timedelta(days=1)).isoformat()
        self.post_mock({'tipo': 'criar_meta', 'titulo': 'X', 'valor_alvo': 1000, 'data_limite': ontem})
        self.assertEqual(Meta.objects.count(), 0)
        self.assertIn('futura', self.ultima_resposta())

    def test_meta_valida_e_contribuicao(self):
        futuro = (timezone.localdate() + timedelta(days=60)).isoformat()
        self.post_mock({'tipo': 'criar_meta', 'titulo': 'Viagem', 'valor_alvo': 3000, 'data_limite': futuro})
        self.assertEqual(Meta.objects.get().valor_alvo, Decimal('3000.00'))
        self.post_mock({'tipo': 'contribuir_meta', 'valor': 500})
        self.assertEqual(Meta.objects.get().valor_atual, Decimal('500.00'))

    def test_contribuicao_invalida_nao_altera_meta(self):
        Meta.objects.create(usuario=self.usuario, titulo='M', valor_alvo=100)
        self.post_mock({'tipo': 'contribuir_meta', 'valor': -50})
        self.assertEqual(Meta.objects.get().valor_atual, Decimal('0.00'))


class ValidacaoServiceUnitTests(TestCase):
    def test_parse_valor(self):
        self.assertEqual(parse_valor('1,5'), Decimal('1.50'))
        self.assertEqual(parse_valor(0.1 + 0.2), Decimal('0.30'))
        self.assertIsNone(parse_valor(-1))
        self.assertIsNone(parse_valor(100000000))

    def test_parse_data_e_id(self):
        self.assertEqual(parse_data('2026-01-31'), date(2026, 1, 31))
        self.assertIsNone(parse_data('lixo'))
        self.assertEqual(parse_id('7'), 7)
        self.assertIsNone(parse_id('x'))

    def test_normalizar_categoria(self):
        self.assertEqual(normalizar_categoria(' SAUDE '), 'saude')
        self.assertEqual(normalizar_categoria(None), 'outros')

    def test_validar_transacao_ok(self):
        dados, erro = validar_transacao({'tipo': 'receita', 'valor': 100, 'categoria': 'x', 'descricao': 'Salario'})
        self.assertIsNone(erro)
        self.assertEqual(dados['categoria'], 'outros')
