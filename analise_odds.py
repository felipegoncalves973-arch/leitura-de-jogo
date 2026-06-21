import os
import requests
import json
from datetime import datetime

# Carrega variáveis de ambiente exatamente como no arquivo YAML
ODDS_API_KEY = os.environ.get('ODDS_API_KEY')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# Configurações da API The Odds API
SPORT = 'soccer'
REGIONS = 'eu'
MARKETS = 'h2h'
ODDS_FORMAT = 'decimal'
DATE_FORMAT = 'iso'


def check_env_variables():
    """Verifica se todas as variáveis de ambiente necessárias estão definidas."""
    missing = []
    if not ODDS_API_KEY:
        missing.append('ODDS_API_KEY')
    if not TELEGRAM_BOT_TOKEN:
        missing.append('TELEGRAM_BOT_TOKEN')
    if not TELEGRAM_CHAT_ID:
        missing.append('TELEGRAM_CHAT_ID')

    if missing:
        raise EnvironmentError(
            f"Variáveis de ambiente não definidas: {', '.join(missing)}"
        )


def get_odds():
    """Obtém as odds de futebol da The Odds API."""
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds"
    params = {
        'apiKey': ODDS_API_KEY,
        'regions': REGIONS,
        'markets': MARKETS,
        'oddsFormat': ODDS_FORMAT,
        'dateFormat': DATE_FORMAT,
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def calculate_implied_probabilities(odds_list):
    """Calcula as probabilidades implícitas a partir das odds decimais."""
    return [1 / odd for odd in odds_list]


def calculate_overround(implied_probabilities):
    """Calcula o overround (soma das probabilidades implícitas)."""
    return sum(implied_probabilities) - 1


def format_alert(match, bookmaker, odds_list, implied_probabilities, overround):
    """Formata o alerta para envio ao Telegram."""
    home_team = match.get('home_team', 'N/A')
    away_team = match.get('away_team', 'N/A')
    bookmaker_name = bookmaker.get('title', 'N/A')
    match_time = match.get('commence_time', 'N/A')

    outcome_names = ['Casa', 'Empate', 'Fora']
    if len(odds_list) == 2:
        outcome_names = ['Casa', 'Fora']

    lines = [
        "⚽ *Alerta de Odds - Futebol*",
        f"*Partida:* {home_team} vs {away_team}",
        f"*Data/Hora:* {match_time}",
        f"*Bookmaker:* {bookmaker_name}",
        "",
        "*Odds e Probabilidades Implícitas:*",
    ]

    for i, (odd, prob) in enumerate(zip(odds_list, implied_probabilities)):
        label = outcome_names[i] if i < len(outcome_names) else f"Outcome {i+1}"
        lines.append(f"{label}: Odd `{odd:.2f}` | Prob `{prob:.2%}`")

    lines.append("")
    lines.append(f"*Overround:* `{overround:.2%}`")

    return "\n".join(lines)


def send_telegram_message(message):
    """Envia a mensagem formatada para o Telegram via Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown',
    }

    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def main():
    try:
        check_env_variables()
        matches = get_odds()

        if not matches:
            print("Nenhuma partida encontrada.")
            return

        # Processa cada partida e bookmaker
        for match in matches:
            bookmakers = match.get('bookmakers', [])
            for bookmaker in bookmakers:
                markets = bookmaker.get('markets', [])
                for market in markets:
                    outcomes = market.get('outcomes', [])
                    if not outcomes:
                        continue

                    odds_list = [outcome.get('price', 0) for outcome in outcomes]

                    if any(odd <= 0 for odd in odds_list):
                        continue

                    implied_probabilities = calculate_implied_probabilities(odds_list)
                    overround = calculate_overround(implied_probabilities)

                    alert = format_alert(
                        match, bookmaker, odds_list, implied_probabilities, overround
                    )

                    send_telegram_message(alert)
                    print(f"Alerta enviado: {match.get('home_team')} vs {match.get('away_team')}")

    except requests.exceptions.RequestException as e:
        print(f"Erro na requisição HTTP: {e}")
    except EnvironmentError as e:
        print(f"Erro de configuração: {e}")
    except Exception as e:
        print(f"Erro inesperado: {e}")


if __name__ == '__main__':
    main()
