from django.utils import timezone
from django.db.models import Sum
from core.models import Meta, Transacao
from core.services.formatacao_service import formatar_valor

def _barra_progresso(atual, alvo):
    if alvo <= 0: return '⬜' * 10
    porcentagem = min(100, int((atual / alvo) * 100))
    blocos_cheios = int(porcentagem / 10)
    blocos_vazios = 10 - blocos_cheios
    return '🟩' * blocos_cheios + '⬜' * blocos_vazios + f" {porcentagem}%"

def criar_meta(usuario, titulo, valor_alvo, data_limite=None):
    # Guarda um resumo da meta anterior (se existir) antes de substitui-la, para avisar o usuario
    anterior = Meta.objects.filter(usuario=usuario).first()
    meta_anterior = None
    if anterior:
        meta_anterior = {
            'titulo': anterior.titulo,
            'valor_atual': anterior.valor_atual,
            'valor_alvo': anterior.valor_alvo,
        }
    Meta.objects.filter(usuario=usuario).delete()
    
    nova_meta = Meta.objects.create(
        usuario=usuario,
        titulo=titulo,
        valor_alvo=valor_alvo,
        data_limite=data_limite
    )
    
    texto = f"🎯 *Nova meta criada: {titulo}*\nAlvo: {formatar_valor(valor_alvo)}\n"
    
    if data_limite:
        texto += f"Prazo: {data_limite.strftime('%d/%m/%Y')}\n\n"
        if usuario.orcamento_mensal <= 0:
            texto += "⚠️ Para eu poder te ajudar a calcular o limite diario de gastos, defina seu orcamento mensal."
        else:
            limite = calcular_limite_diario(usuario)
            if limite:
                texto += (
                    f"Para bater essa meta, seu limite MAXIMO de gasto por dia é de *{formatar_valor(limite['limite_diario'])}*.\n"
                    f"A partir de agora, te avisarei como está esse limite após cada gasto."
                )
    else:
        texto += "Sem data definida. Acompanharemos o % do seu progresso!"
        
    if meta_anterior:
        texto += (
            f"\n\n⚠️ Esta meta substituiu a anterior, *{meta_anterior['titulo']}* "
            f"(guardado: {formatar_valor(meta_anterior['valor_atual'])} de {formatar_valor(meta_anterior['valor_alvo'])})."
        )

    return nova_meta, texto

def contribuir_para_meta(usuario, valor):
    meta = Meta.objects.filter(usuario=usuario).first()
    if not meta:
        return "❌ Voce nao tem nenhuma meta ativa no momento."
    
    meta.valor_atual += valor
    meta.save()
    
    progresso = _barra_progresso(meta.valor_atual, meta.valor_alvo)
    falta = max(0, meta.valor_alvo - meta.valor_atual)
    
    return (
        f"🎉 *Contribuicao Registrada!*\n"
        f"Voce adicionou {formatar_valor(valor)} a sua meta '{meta.titulo}'.\n\n"
        f"Progresso:\n{progresso}\n\n"
        f"Falta: {formatar_valor(falta)}"
    )

def consultar_status_meta(usuario):
    meta = Meta.objects.filter(usuario=usuario).first()
    if not meta:
        return "❌ Voce nao tem nenhuma meta ativa no momento."
        
    progresso = _barra_progresso(meta.valor_atual, meta.valor_alvo)
    falta = max(0, meta.valor_alvo - meta.valor_atual)
    
    texto = (
        f"🎯 *Status da Meta: {meta.titulo}*\n\n"
        f"Alvo: {formatar_valor(meta.valor_alvo)}\n"
        f"Atual: {formatar_valor(meta.valor_atual)}\n\n"
        f"Progresso:\n{progresso}\n\n"
        f"Falta: {formatar_valor(falta)}"
    )
    
    limite = calcular_limite_diario(usuario)
    if limite:
        texto += f"\n\n💳 *Limite de gasto para hoje:* {formatar_valor(limite['restante_hoje'])}"
        
    return texto

def calcular_limite_diario(usuario):
    meta = Meta.objects.filter(usuario=usuario).first()
    if not meta or not meta.data_limite or usuario.orcamento_mensal <= 0:
        return None
        
    hoje = timezone.localdate()
    dias_restantes = (meta.data_limite - hoje).days
    
    if dias_restantes <= 0 or meta.valor_atual >= meta.valor_alvo:
        return None
        
    falta = float(meta.valor_alvo) - float(meta.valor_atual)
    economia_diaria = falta / dias_restantes
    
    # Limite diario teorico (orcamento / 30 - economia_diaria)
    limite_diario = max(0, (float(usuario.orcamento_mensal) / 30) - economia_diaria)
    
    # Quanto ja gastou hoje
    gastos_hoje = Transacao.objects.filter(
        usuario=usuario, 
        tipo='despesa', 
        data=hoje
    ).aggregate(Sum('valor'))['valor__sum'] or 0.0
    
    restante_hoje = float(limite_diario) - float(gastos_hoje)
    
    return {
        'limite_diario': limite_diario,
        'gastos_hoje': float(gastos_hoje),
        'restante_hoje': restante_hoje
    }

def texto_aviso_limite_diario(usuario):
    limite = calcular_limite_diario(usuario)
    if not limite:
        return None
        
    restante = limite['restante_hoje']
    meta = Meta.objects.filter(usuario=usuario).first()
    
    texto = f"\n\n━━━━━━━━━━━━━━\n🎯 Meta: {meta.titulo}\n"
    
    if restante > 0:
        texto += f"✅ Para manter sua meta, voce ainda pode gastar *{formatar_valor(restante)}* hoje."
    elif restante == 0:
        texto += f"⚠️ Você atingiu seu limite exato de gastos para hoje."
    else:
        texto += f"🚨 Você ultrapassou seu limite diário em *{formatar_valor(abs(restante))}*!"
        
    texto += f"\n_(Limite/dia: {formatar_valor(limite['limite_diario'])} | Gasto hoje: {formatar_valor(limite['gastos_hoje'])})_"
    return texto
