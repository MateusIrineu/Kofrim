import hashlib
import hmac
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import MensagemProcessada, Meta, Transacao, Usuario
from core.services.validacao_service import (
    normalizar_categoria, parse_data, parse_id, parse_valor, validar_transacao,
)

TELEFONE = '5511999990000'
OUTRO_TELEFONE = '5511888880000'
URL = '/api/whatsapp/'


@override_settings(DEBUG=True, WHATSAPP_APP_SECRET='')
class WebhookTestBase(TestCase):
    """Base: envia mensagens pelo webhook usando o modo !mock (DEBUG=True) e captura as respostas."""

    def setUp(self):
        patcher = patch('core.views.enviar_mensagem_whatsapp')
        self.enviar = patcher.start()
        self.addCleanup(patcher.stop)
        self.usuario = Usuario.objects.create(telefone=TELEFONE, orcamento_mensal=5000)

    def post_texto(self, texto, telefone=TELEFONE, message_id=None, headers=None):
        msg = {'from': telefone, 'text': {'body': texto}}
        if message_id is not None:
            msg['id'] = message_id
        payload = {'entry': [{'changes': [{'value': {'messages': [msg]}}]}]}
        extra = headers or {}
        return self.client.post(
            URL,
            data=json.dumps(payload),
            content_type='application/json',
            **extra,
        )

    def post_mock(self, dados, telefone=TELEFONE, message_id=None, headers=None):
        return self.post_texto('!mock ' + json.dumps(dados), telefone, message_id=message_id, headers=headers)

    def ultima_resposta(self):
        return self.enviar.call_args[0][1]


# ==========================================
# ETAPA 1 + CORRECOES ANTERIORES
# ==========================================

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


class ExtratoQuantidadeTests(WebhookTestBase):
    def test_extrato_ultimos_dois_meses_inclui_mes_anterior(self):
        hoje = timezone.localdate()
        primeiro_deste_mes = hoje.replace(day=1)
        mes_passado = primeiro_deste_mes - timedelta(days=10)
        tres_meses_atras = primeiro_deste_mes - timedelta(days=75)

        Transacao.objects.create(
            usuario=self.usuario, tipo='despesa', valor=20,
            categoria='servicos', descricao='Netflix Setembro', data=mes_passado,
        )
        Transacao.objects.create(
            usuario=self.usuario, tipo='despesa', valor=50,
            categoria='alimentacao', descricao='Lanche Atual', data=hoje,
        )
        Transacao.objects.create(
            usuario=self.usuario, tipo='despesa', valor=99,
            categoria='outros', descricao='Muito Antigo', data=tres_meses_atras,
        )

        # Apenas mes atual (quantidade=1)
        self.post_mock({'tipo': 'extrato', 'subtipo': 'despesas', 'periodo': 'mes', 'quantidade': 1})
        resp_1 = self.ultima_resposta()
        self.assertIn('Lanche Atual', resp_1)
        self.assertNotIn('Netflix Setembro', resp_1)

        # Ultimos 2 meses (quantidade=2)
        self.post_mock({'tipo': 'extrato', 'subtipo': 'despesas', 'periodo': 'mes', 'quantidade': 2})
        resp_2 = self.ultima_resposta()
        self.assertIn('dos ultimos 2 meses', resp_2)
        self.assertIn('Lanche Atual', resp_2)
        self.assertIn('Netflix Setembro', resp_2)
        self.assertNotIn('Muito Antigo', resp_2)


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


# ==========================================
# ETAPA 2: SEGURANCA E INTEGRIDADE
# ==========================================

class ItemQuatroMockRestritoDebugTests(WebhookTestBase):
    @override_settings(DEBUG=False, WHATSAPP_APP_SECRET='segredo-teste')
    def test_mock_bloqueado_quando_debug_false(self):
        corpo = json.dumps({
            'entry': [{'changes': [{'value': {'messages': [{
                'from': TELEFONE,
                'text': {'body': '!mock {"tipo": "despesa", "valor": 999, "categoria": "outros", "descricao": "Hack"}'}
            }]}}]}]
        }).encode('utf-8')
        assinatura = 'sha256=' + hmac.new(b'segredo-teste', corpo, hashlib.sha256).hexdigest()

        with patch('core.views.analisar_mensagem') as mock_gemini:
            resp = self.client.post(
                URL,
                data=corpo,
                content_type='application/json',
                HTTP_X_HUB_SIGNATURE_256=assinatura,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Transacao.objects.count(), 0)
        mock_gemini.assert_not_called()


class ItemCincoValidacaoWebhookMetaTests(WebhookTestBase):
    @override_settings(WHATSAPP_VERIFY_TOKEN='meu-token-secreto')
    def test_get_verificacao_meta_sucesso(self):
        resp = self.client.get(URL, {
            'hub.mode': 'subscribe',
            'hub.verify_token': 'meu-token-secreto',
            'hub.challenge': '1234567890',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.decode(), '1234567890')

    @override_settings(WHATSAPP_VERIFY_TOKEN='meu-token-secreto')
    def test_get_verificacao_meta_token_invalido_retorna_403(self):
        resp = self.client.get(URL, {
            'hub.mode': 'subscribe',
            'hub.verify_token': 'token-errado',
            'hub.challenge': '1234567890',
        })
        self.assertEqual(resp.status_code, 403)

    def test_get_simples_sem_parametros_retorna_200(self):
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)

    @override_settings(DEBUG=False, WHATSAPP_APP_SECRET='segredo-hmac')
    def test_post_sem_assinatura_ou_invalida_retorna_403(self):
        corpo = json.dumps({'entry': []}).encode('utf-8')
        resp_sem = self.client.post(URL, data=corpo, content_type='application/json')
        self.assertEqual(resp_sem.status_code, 403)

        resp_invalida = self.client.post(
            URL,
            data=corpo,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256='sha256=invalida',
        )
        self.assertEqual(resp_invalida.status_code, 403)

    @override_settings(DEBUG=True, WHATSAPP_APP_SECRET='segredo-hmac')
    def test_post_com_assinatura_valida_processa_normalmente(self):
        payload = {
            'entry': [{'changes': [{'value': {'messages': [{
                'from': TELEFONE,
                'text': {'body': '!mock {"tipo": "despesa", "valor": 35, "categoria": "alimentacao", "descricao": "Almoco"}'}
            }]}}]}]
        }
        corpo = json.dumps(payload).encode('utf-8')
        assinatura = 'sha256=' + hmac.new(b'segredo-hmac', corpo, hashlib.sha256).hexdigest()

        resp = self.client.post(
            URL,
            data=corpo,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=assinatura,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Transacao.objects.count(), 1)

    @override_settings(DEBUG=False, WHATSAPP_APP_SECRET='')
    def test_post_em_producao_sem_app_secret_configurado_bloqueia(self):
        corpo = json.dumps({'entry': []}).encode('utf-8')
        resp = self.client.post(URL, data=corpo, content_type='application/json')
        self.assertEqual(resp.status_code, 403)


class ItemSeisIdempotenciaTests(WebhookTestBase):
    def test_mensagem_com_mesmo_id_nao_duplica_transacao(self):
        dados = {'tipo': 'despesa', 'valor': 45, 'categoria': 'alimentacao', 'descricao': 'Pizza'}
        resp1 = self.post_mock(dados, message_id='wamid.ABC123456')
        resp2 = self.post_mock(dados, message_id='wamid.ABC123456')

        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()['status'], 'duplicado')
        self.assertEqual(Transacao.objects.count(), 1)
        self.assertEqual(MensagemProcessada.objects.filter(message_id='wamid.ABC123456').count(), 1)

    def test_mensagens_com_ids_diferentes_sao_processadas(self):
        dados = {'tipo': 'despesa', 'valor': 25, 'categoria': 'alimentacao', 'descricao': 'Lanche'}
        self.post_mock(dados, message_id='wamid.MSG_001')
        self.post_mock(dados, message_id='wamid.MSG_002')
        self.assertEqual(Transacao.objects.count(), 2)
