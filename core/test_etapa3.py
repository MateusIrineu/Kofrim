import hashlib
import hmac
import json
import urllib.error
from decimal import Decimal
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core import views
from core.models import Meta, Transacao, Usuario
from core.services import whatsapp_service
from core.services.whatsapp_service import _dividir_mensagem, enviar_mensagem_whatsapp
from core.tests import TELEFONE, URL, WebhookTestBase


# ==========================================
# ITEM 9: ENVIO REAL PELA WHATSAPP CLOUD API
# ==========================================

def _resposta_http(status=200):
    resposta = MagicMock()
    resposta.status = status
    resposta.__enter__.return_value = resposta
    resposta.__exit__.return_value = False
    return resposta


class ItemNoveEnvioWhatsAppTests(TestCase):
    @override_settings(WHATSAPP_ACCESS_TOKEN='', WHATSAPP_PHONE_NUMBER_ID='')
    def test_sem_credenciais_usa_simulador_e_nao_chama_a_api(self):
        with patch('core.services.whatsapp_service.urllib.request.urlopen') as urlopen:
            self.assertTrue(enviar_mensagem_whatsapp('5511999990000', 'oi'))
        urlopen.assert_not_called()

    @override_settings(WHATSAPP_ACCESS_TOKEN='TOKEN-X', WHATSAPP_PHONE_NUMBER_ID='123456')
    def test_com_credenciais_envia_payload_correto_para_a_graph_api(self):
        with patch('core.services.whatsapp_service.urllib.request.urlopen',
                   return_value=_resposta_http(200)) as urlopen:
            ok = enviar_mensagem_whatsapp('5511999990000', 'Ola, mundo')

        self.assertTrue(ok)
        requisicao = urlopen.call_args[0][0]
        self.assertEqual(requisicao.full_url, 'https://graph.facebook.com/v21.0/123456/messages')
        self.assertEqual(requisicao.get_method(), 'POST')
        self.assertEqual(requisicao.get_header('Authorization'), 'Bearer TOKEN-X')
        corpo = json.loads(requisicao.data)
        self.assertEqual(corpo['messaging_product'], 'whatsapp')
        self.assertEqual(corpo['to'], '5511999990000')
        self.assertEqual(corpo['text']['body'], 'Ola, mundo')

    @override_settings(WHATSAPP_ACCESS_TOKEN='TOKEN-X', WHATSAPP_PHONE_NUMBER_ID='123456')
    def test_erro_http_retorna_false_sem_levantar_excecao(self):
        erro = urllib.error.HTTPError('url', 401, 'Unauthorized', {}, None)
        erro.read = lambda: b'{"error": "invalid token"}'
        with patch('core.services.whatsapp_service.urllib.request.urlopen', side_effect=erro):
            self.assertFalse(enviar_mensagem_whatsapp('5511999990000', 'oi'))

    @override_settings(WHATSAPP_ACCESS_TOKEN='TOKEN-X', WHATSAPP_PHONE_NUMBER_ID='123456')
    def test_falha_de_rede_retorna_false_sem_levantar_excecao(self):
        with patch('core.services.whatsapp_service.urllib.request.urlopen',
                   side_effect=urllib.error.URLError('sem rede')):
            self.assertFalse(enviar_mensagem_whatsapp('5511999990000', 'oi'))

    @override_settings(WHATSAPP_ACCESS_TOKEN='TOKEN-X', WHATSAPP_PHONE_NUMBER_ID='123456')
    def test_mensagem_longa_e_enviada_em_varios_blocos(self):
        texto = '\n'.join(f'linha {i} ' + 'x' * 90 for i in range(100))  # ~10 mil caracteres
        with patch('core.services.whatsapp_service.urllib.request.urlopen',
                   return_value=_resposta_http(200)) as urlopen:
            self.assertTrue(enviar_mensagem_whatsapp('5511999990000', texto))
        self.assertGreaterEqual(urlopen.call_count, 3)
        enviados = [json.loads(c[0][0].data)['text']['body'] for c in urlopen.call_args_list]
        self.assertTrue(all(len(b) <= whatsapp_service.LIMITE_CARACTERES for b in enviados))
        self.assertEqual('\n'.join(enviados), texto)

    def test_dividir_mensagem_curta_e_linha_gigante(self):
        self.assertEqual(_dividir_mensagem('curta'), ['curta'])
        blocos = _dividir_mensagem('a' * 9000, limite=4000)
        self.assertEqual([len(b) for b in blocos], [4000, 4000, 1000])


# ==========================================
# ITEM 10: PROCESSAMENTO ASSINCRONO
# ==========================================

class ItemDezAssincronoTests(WebhookTestBase):
    @override_settings(WEBHOOK_ASSINCRONO=True)
    def test_modo_assincrono_responde_na_hora_e_delega_para_thread(self):
        with patch('core.views.threading.Thread') as thread:
            resp = self.post_mock({'tipo': 'despesa', 'valor': 10, 'categoria': 'outros', 'descricao': 'x'})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'recebido')
        self.assertEqual(Transacao.objects.count(), 0)  # ainda nao processou: ficou para a thread
        kwargs = thread.call_args.kwargs
        self.assertIs(kwargs['target'], views._processar_em_segundo_plano)
        self.assertEqual(kwargs['args'][0], TELEFONE)
        self.assertTrue(kwargs['daemon'])
        thread.return_value.start.assert_called_once()

    def test_funcao_de_segundo_plano_processa_e_fecha_a_conexao(self):
        texto = '!mock ' + json.dumps({'tipo': 'despesa', 'valor': 12, 'categoria': 'outros', 'descricao': 'x'})
        with patch('core.views.connection') as conexao:
            views._processar_em_segundo_plano(TELEFONE, texto)
        self.assertEqual(Transacao.objects.count(), 1)
        conexao.close.assert_called_once()

    def test_erro_em_segundo_plano_nao_propaga_e_avisa_o_usuario(self):
        texto = '!mock ' + json.dumps({'tipo': 'despesa', 'valor': 12, 'categoria': 'outros', 'descricao': 'x'})
        with patch('core.views.connection'), \
                patch('core.views.Transacao.objects.create', side_effect=RuntimeError('boom')):
            views._processar_em_segundo_plano(TELEFONE, texto)  # nao pode levantar
        self.assertEqual(self.ultima_resposta(), views.MENSAGEM_ERRO_INTERNO)


# ==========================================
# ITEM 11: AJUDA E FALHA DA IA
# ==========================================

class ItemOnzeFeedbackTests(WebhookTestBase):
    def test_mensagem_nao_financeira_recebe_menu_de_ajuda(self):
        with patch('core.views.analisar_mensagem', return_value={'tipo': 'erro', 'mensagem': 'saudacao'}):
            resp = self.post_texto('Oi, bom dia!', telefone='5511777770000')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.ultima_resposta(), views.MENSAGEM_AJUDA)
        self.assertFalse(Usuario.objects.filter(telefone='5511777770000').exists())

    def test_falha_tecnica_da_ia_recebe_aviso_de_tentar_novamente(self):
        retorno = {'tipo': 'erro', 'mensagem': 'Falha na extracao de dados.', 'falha_tecnica': True}
        with patch('core.views.analisar_mensagem', return_value=retorno):
            self.post_texto('Gastei 45 de pizza')
        self.assertEqual(self.ultima_resposta(), views.MENSAGEM_FALHA_IA)
        self.assertNotEqual(self.ultima_resposta(), views.MENSAGEM_AJUDA)

    def test_resposta_da_ia_em_formato_inesperado_vira_falha_tecnica(self):
        with patch('core.views.analisar_mensagem', return_value=['nao', 'e', 'dict']):
            self.post_texto('qualquer coisa')
        self.assertEqual(self.ultima_resposta(), views.MENSAGEM_FALHA_IA)

    def test_gemini_service_marca_falha_tecnica(self):
        from core.services import gemini_service
        with patch.object(gemini_service.client.models, 'generate_content', side_effect=RuntimeError('429')):
            resultado = gemini_service.analisar_mensagem('oi')
        self.assertEqual(resultado['tipo'], 'erro')
        self.assertTrue(resultado['falha_tecnica'])

    @override_settings(DEBUG=False, WHATSAPP_APP_SECRET='segredo-teste')
    def test_mock_bloqueado_em_producao_nao_responde_nada(self):
        corpo = json.dumps({'entry': [{'changes': [{'value': {'messages': [{
            'from': TELEFONE,
            'text': {'body': '!mock {"tipo": "despesa", "valor": 9, "descricao": "x"}'},
        }]}}]}]}).encode('utf-8')
        assinatura = 'sha256=' + hmac.new(b'segredo-teste', corpo, hashlib.sha256).hexdigest()
        self.client.post(URL, data=corpo, content_type='application/json',
                         HTTP_X_HUB_SIGNATURE_256=assinatura)
        self.enviar.assert_not_called()
        self.assertEqual(Transacao.objects.count(), 0)


# ==========================================
# ITEM 12: MIDIA / TIPOS NAO SUPORTADOS
# ==========================================

class ItemDozeMidiaTests(WebhookTestBase):
    def _post_tipo(self, tipo):
        payload = {'entry': [{'changes': [{'value': {'messages': [
            {'from': '5511666660000', 'id': f'wamid.{tipo}', 'type': tipo, tipo: {'id': 'media-1'}}
        ]}}]}]}
        return self.client.post(URL, data=json.dumps(payload), content_type='application/json')

    def test_audio_imagem_e_figurinha_recebem_aviso_de_somente_texto(self):
        for tipo in ['audio', 'image', 'sticker', 'video', 'document']:
            with self.subTest(tipo=tipo), patch('core.views.analisar_mensagem') as gemini:
                resp = self._post_tipo(tipo)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()['status'], 'tipo_nao_suportado')
                self.assertEqual(self.ultima_resposta(), views.MENSAGEM_SO_TEXTO)
                gemini.assert_not_called()
        self.assertFalse(Usuario.objects.filter(telefone='5511666660000').exists())

    def test_mensagem_de_texto_com_type_text_continua_funcionando(self):
        payload = {'entry': [{'changes': [{'value': {'messages': [{
            'from': TELEFONE, 'id': 'wamid.txt', 'type': 'text',
            'text': {'body': '!mock ' + json.dumps({'tipo': 'despesa', 'valor': 5, 'descricao': 'x'})},
        }]}}]}]}
        self.client.post(URL, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(Transacao.objects.count(), 1)


# ==========================================
# ITEM 13: DEFINIR ORCAMENTO
# ==========================================

class ItemTrezeOrcamentoTests(WebhookTestBase):
    def test_define_orcamento_mensal(self):
        self.post_mock({'tipo': 'definir_orcamento', 'valor': 7500})
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.orcamento_mensal, Decimal('7500.00'))
        self.assertIn('R$ 7.500,00', self.ultima_resposta())

    def test_orcamento_invalido_nao_altera_o_valor_atual(self):
        for valor in [-100, 0, 'abc', None]:
            with self.subTest(valor=valor):
                self.post_mock({'tipo': 'definir_orcamento', 'valor': valor})
                self.usuario.refresh_from_db()
                self.assertEqual(self.usuario.orcamento_mensal, Decimal('5000.00'))
                self.assertIn('Nao entendi', self.ultima_resposta())

    def test_novo_usuario_pode_definir_orcamento_e_comeca_zerado_antes(self):
        self.post_mock({'tipo': 'definir_orcamento', 'valor': 3000}, telefone='5511555550000')
        novo = Usuario.objects.get(telefone='5511555550000')
        self.assertEqual(novo.orcamento_mensal, Decimal('3000.00'))

    def test_orcamento_com_meta_ativa_informa_limite_diario(self):
        self.usuario.orcamento_mensal = 0
        self.usuario.save()
        Meta.objects.create(usuario=self.usuario, titulo='Viagem', valor_alvo=3000,
                            data_limite=timezone.localdate() + timedelta(days=100))
        self.post_mock({'tipo': 'definir_orcamento', 'valor': 6000})
        self.assertIn('limite de gasto por dia', self.ultima_resposta())

    def test_prompt_do_gemini_conhece_a_intencao(self):
        from core.services.gemini_service import instrucao_sistema
        self.assertIn('definir_orcamento', instrucao_sistema)


# ==========================================
# ITEM 14: AVISO AO SUBSTITUIR META
# ==========================================

class ItemQuatorzeMetaSubstituidaTests(WebhookTestBase):
    def _futuro(self, dias=90):
        return (timezone.localdate() + timedelta(days=dias)).isoformat()

    def test_primeira_meta_nao_tem_aviso_de_substituicao(self):
        self.post_mock({'tipo': 'criar_meta', 'titulo': 'Viagem', 'valor_alvo': 3000, 'data_limite': self._futuro()})
        self.assertNotIn('substituiu', self.ultima_resposta())

    def test_segunda_meta_avisa_que_substituiu_a_anterior_com_o_progresso(self):
        self.post_mock({'tipo': 'criar_meta', 'titulo': 'Viagem', 'valor_alvo': 3000, 'data_limite': self._futuro()})
        self.post_mock({'tipo': 'contribuir_meta', 'valor': 500})
        self.post_mock({'tipo': 'criar_meta', 'titulo': 'Carro', 'valor_alvo': 20000, 'data_limite': self._futuro(200)})

        resposta = self.ultima_resposta()
        self.assertIn('substituiu a anterior', resposta)
        self.assertIn('Viagem', resposta)
        self.assertIn('R$ 500,00', resposta)
        self.assertIn('R$ 3.000,00', resposta)
        meta = Meta.objects.get()
        self.assertEqual((meta.titulo, meta.valor_atual), ('Carro', Decimal('0.00')))
