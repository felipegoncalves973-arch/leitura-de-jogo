import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# CONFIGURAÇÃO / INSTRUÇÕES
# =============================================================================
# 1. Obtenha uma chave de API gratuita em: https://the-odds-api.com/
#    - Cadastre-se com e-mail e confirme a conta.
#    - A chave free tem limites de requisições/mês (verifique o plano).
# 2. Configure as variáveis de ambiente abaixo.
#    Opção A - arquivo .env na raiz do projeto (recomendado):
#      THE_ODDS_API_KEY=sua_chave_aqui
#      TELEGRAM_BOT_TOKEN=seu_token_aqui
#      TELEGRAM_CHAT_ID=seu_chat_id_aqui
#    Opção B - exportar no terminal:
#      Linux/macOS: export THE_ODDS_API_KEY=sua_chave_aqui
#      Windows: set THE_ODDS_API_KEY=sua_chave_aqui
# 3. Como obter o Telegram Bot Token e Chat ID:
#    - Abra o @BotFather no Telegram, crie um bot e copie o token.
#    - Envie uma mensagem no chat/canal e use:
#      https://api.telegram.org/bot<<TOKEN>/getUpdates para descobrir o chat_id.
#    - Para canal, adicione o bot como administrador e use @nomeDoCanal ou ID numérico.
# =============================================================================

THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://api.the-odds-api.com/v4"

# Região e mercado de odds. "eu" costuma ter odds decimais; "h2h" = 1X2 (match odds).
REGION = "eu"           # alternativas: us, uk, au
MARKET = "h2h"          # head-to-head / 1X2
SPORT = "soccer"        # opcional filtro; endpoint sports lista esportes disponíveis

# Limite de arbitragem/margem para alerta. Overround típico = 1.0 (100%).
OVERROUND_THRESHOLD = 1.08  # 108% -> mercado com margem alta


def validate_env():
    missing = []
    if not THE_ODDS_API_KEY:
        missing.append("THE_ODDS_API_KEY")
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        print(f"Erro: variáveis de ambiente não configuradas: {', '.join(missing)}")
        print("Configure um arquivo .env ou exporte as variáveis conforme instruções no topo do script.")
        sys.exit(1)


def get_soccer_sport_key():
    """Retorna a chave do primeiro esporte de futebol disponível."""
    url = f"{BASE_URL}/sports"
    params = {"apiKey": THE_ODDS_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        sports = response.json()
        soccer_keys = [s["key"] for s in sports if "soccer" in s.get("key", "")]
        if not soccer_keys:
            print("Nenhum esporte de futebol disponível no momento.")
            sys.exit(1)
        return soccer_keys[0]
    except requests.exceptions.RequestException as e:
        print(f"Erro ao listar esportes: {e}")
        sys.exit(1)


def get_odds(sport_key):
    """Obtém odds do mercado 1X2 (h2h) para o esporte escolhido."""
    url = f"{BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": THE_ODDS_API_KEY,
        "regions": REGION,
        "markets": MARKET,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Erro ao obter odds: {e}")
        sys.exit(1)


def calculate_implied_probabilities(odds_list):
    """
    Converte odds decimais em probabilidades implícitas e normaliza pela margem da casa.
    Retorna dict com: raw_probs, fair_probs, overround.
    """
    if not odds_list or len(odds_list) < 2:
        return None
    raw_probs = [1 / odd for odd in odds_list]
    overround = sum(raw_probs)
    fair_probs = [p / overround for p in raw_probs]
    return {
        "raw_probs": raw_probs,
        "fair_probs": fair_probs,
        "overround": overround,
    }


def format_analysis(event):
    """Formata a análise de uma partida com odds e probabilidades."""
    home_team = event.get("home_team", "N/A")
    away_team = event.get("away_team", "N/A")
    commence_time = event.get("commence_time", "N/A")
    bookmakers = event.get("bookmakers", [])
    if not bookmakers:
        return None

    # Pega o primeiro bookmaker disponível
    bookmaker = bookmakers[0]
    bookmaker_title = bookmaker.get("title", "N/A")
    markets = bookmaker.get("markets", [])
    h2h_market = next((m for m in markets if m.get("key") == "h2h"), None)
    if not h2h_market:
        return None

    outcomes = h2h_market.get("outcomes", [])
    if len(outcomes) < 2:
        return None

    # Determina ordem: casa, empate (se 3), fora
    odds_map = {}
    for outcome in outcomes:
        name = outcome.get("name")
        price = outcome.get("price")
        odds_map[name] = price

    home_odd = odds_map.get(home_team)
    away_odd = odds_map.get(away_team)
    draw_odd = odds_map.get("Draw") or odds_map.get("Empate")

    if not home_odd or not away_odd:
        return None

    odds_list = [home_odd, away_odd]
    labels = [f"Casa ({home_team})", f"Fora ({away_team})"]
    if draw_odd:
        odds_list.insert(1, draw_odd)
        labels.insert(1, "Empate")

    calc = calculate_implied_probabilities(odds_list)
    if not calc:
        return None

    overround_pct = (calc["overround"] - 1) * 100
    fair_pct = [p * 100 for p in calc["fair_probs"]]

    lines = [
        f"⚽ *{home_team}* vs *{away_team}*",
        f"📅 Início: {commence_time}",
        f"🏦 Casa: {bookmaker_title}",
        "",
        "*Odds (1X2):*",
    ]
    for label, odd, prob in zip(labels, odds_list, fair_pct):
        lines.append(f"  • {label}: {odd} -> {prob:.2f}%")
    lines.append("")
    lines.append(f"*Margem da casa (overround):* {calc['overround']:.4f} ({overround_pct:+.2f}%)")
    if calc["overround"] >= OVERROUND_THRESHOLD:
        lines.append("⚠️ Mercado com margem *elevada*. Cuidado com valor.")
    else:
        lines.append("✅ Margem dentro da faixa considerada justa.")

    return "\n".join(lines), calc["overround"]


def send_telegram_message(message):
    """Envia mensagem formatada para o Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            print("Mensagem enviada com sucesso para o Telegram.")
        else:
            print(f"Falha ao enviar mensagem: {data}")
    except requests.exceptions.RequestException as e:
        print(f"Erro ao enviar mensagem para o Telegram: {e}")


def main():
    validate_env()

    sport_key = get_soccer_sport_key()
    print(f"Esporte selecionado: {sport_key}")

    events = get_odds(sport_key)
    if not events:
        print("Nenhum evento disponível no momento.")
        return

    analyses = []
    for event in events:
        analysis = format_analysis(event)
        if analysis:
            analyses.append(analysis)

    if not analyses:
        print("Nenhuma análise válida pôde ser gerada.")
        return

    # Ordena por menor margem (mais valor potencial) e pega as 5 melhores
    analyses.sort(key=lambda x: x[1])
    top_analyses = analyses[:5]

    header = "🔥 *Análise de Odds de Futebol - The Odds API*\n\n"
    body = "\n\n".join([a[0] for a in top_analyses])
    footer = (
        "\n\n_Dados fornecidos por The Odds API (https://the-odds-api.com/)._"
    )
    full_message = header + body + footer

    # Telegram tem limite de 4096 caracteres por mensagem
    if len(full_message) > 4000:
        full_message = full_message[:3990] + "\n..."

    send_telegram_message(full_message)


if __name__ == "__main__":
    main()
