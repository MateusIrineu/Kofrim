def formatar_valor(valor):
    valor_formatado = f'{valor:,.2f}'.replace(",", "X").replace(".",",").replace("X",".")
    return f'R$ {valor_formatado}'