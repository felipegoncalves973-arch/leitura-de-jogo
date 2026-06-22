import os
import time
import logging
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Bot

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('analise_odds.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configurações via variáveis de ambiente
ODDS_API_KEY = os.environ.get('ODDS_API_KEY', 'SUA_API_KEY_AQUI')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'SEU_BOT_TOKEN_AQUI')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', 'SEU_CHAT_ID_AQUI')
REGIONS = os.environ.get('REGIONS', 'eu')
ODDS_FORMAT = os.environ.get('ODDS_FORMAT', 'decimal')
DATE_FORMAT = os.environ.get('DATE_FORMAT', 'iso')

# Liga de apostadores (filtro manual)
BOOKMAKERS_PREFERENCE = ['betfair_ex', 'betfair']

# Esportes monitorados
SPORTS = {
    'soccer_fifa_world_cup': 'Copa do Mundo',
    'soccer_brazil_serie_b': 'Série B Brasil'
}

# Mercados solicitados
MARKETS = ['h2h', 'btts', 'totals', 'correct_score', 'totals_h1']

BASE_URL = 'https://api.the-odds-api.com/v4/sports'

# Rate limit
CALLS_PER_SECOND = 1
LAST_CALL_TIME = 0


def rate_limited_request(url, params=None, retries=3, backoff=2):
    """Faz requisição respeitando rate limit e com retry."""
    global LAST_CALL_TIME

    for attempt in range(1, retries + 1):
        try:
            elapsed = time.time() - LAST_CALL_TIME
            if elapsed < 1.0 / CALLS_PER_SECOND:
                time.sleep(1.0 / CALLS_PER_SECOND - elapsed)

            LAST_CALL_TIME = time.time()
            response = requests.get(url, params=params, timeout=30)

            if response.status_code == 429:
                wait = backoff * attempt
                logger.warning(f'Rate limit atingido. Aguardando {wait}s...')
                time.sleep(wait)
                continue

            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f'Erro na requisição (tentativa {attempt}/{retries}): {e}')
            time.sleep(backoff * attempt)

    return None


def get_events(sport_key):
    """Busca eventos do esporte nas próximas 24 horas."""
    now = datetime.utcnow()
    in_24h = now + timedelta(hours=24)

    url = f'{BASE_URL}/{sport_key}/events'
    params = {
        'apiKey': ODDS_API_KEY,
        'regions': REGIONS,
        'oddsFormat': ODDS_FORMAT,
        'dateFormat': DATE_FORMAT,
        'commenceTimeFrom': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'commenceTimeTo': in_24h.strftime('%Y-%m-%dT%H:%M:%SZ')
    }

    logger.info(f'Buscando eventos para {sport_key} nas próximas 24h')
    events = rate_limited_request(url, params)

    if events is None:
        logger.error(f'Falha ao buscar eventos para {sport_key}')
        return []

    logger.info(f'{len(events)} eventos encontrados para {sport_key}')
    return events
    
    
def get_event_odds(sport_key, event_id):
    """Busca odds detalhadas de um evento específico com múltiplos mercados."""
    url = f'{BASE_URL}/{sport_key}/events/{event_id}/odds'
    params = {
        'apiKey': ODDS_API_KEY,
        'regions': REGIONS,
        'markets': ','.join(MARKETS),
        'oddsFormat': ODDS_FORMAT,
        'dateFormat': DATE_FORMAT,
        'bookmakers': ','.join(BOOKMAKERS_PREFERENCE)
    }

    logger.info(f'Buscando odds para evento {event_id}')
    data = rate_limited_request(url, params)

    if data is None:
        logger.error(f'Falha ao buscar odds para evento {event_id}')
        return None

    return data


def select_bookmaker(odds_data):
    """Seleciona o bookmaker preferido: betfair_ex ou fallback betfair."""
    bookmakers = odds_data.get('bookmakers', [])

    if not bookmakers:
        return None

    for pref in BOOKMAKERS_PREFERENCE:
        for bm in bookmakers:
            if bm.get('key') == pref:
                return bm

    return bookmakers[0]


def extract_markets(bookmaker):
    """Extrai os mercados disponíveis do bookmaker."""
    markets = {}
    if not bookmaker:
        return markets

    for market in bookmaker.get('markets', []):
        key = market.get('key')
        markets[key] = market

    return markets


def format_h2h(market):
    """Formata mercado 1X2."""
    if not market:
        return 'N/A'

    outcomes = {o.get('name'): o.get('price') for o in market.get('outcomes', [])}
    home = outcomes.get('Home', outcomes.get('1', 'N/A'))
    draw = outcomes.get('Draw', outcomes.get('X', 'N/A'))
    away = outcomes.get('Away', outcomes.get('2', 'N/A'))
    return f'Casa {home} | Empate {draw} | Fora {away}'


def format_totals_h1(market):
    """Formata Over 0.5 HT (primeiro tempo)."""
    if not market:
        return 'N/A'

    for outcome in market.get('outcomes', []):
        if outcome.get('name', '').lower() == 'over' and outcome.get('point') == 0.5:
            return f"Over 0.5 HT @ {outcome.get('price')}"

    return 'N/A'


def format_totals(market):
    """Formata Over 2.5 FT."""
    if not market:
        return 'N/A'

    for outcome in market.get('outcomes', []):
        if outcome.get('name', '').lower() == 'over' and outcome.get('point') == 2.5:
            return f"Over 2.5 FT @ {outcome.get('price')}"

    return 'N/A'


def format_btts(market):
    """Formata Ambas Marcam Sim."""
    if not market:
        return 'N/A'

    for outcome in market.get('outcomes', []):
        if outcome.get('name', '').lower() in ['yes', 'sim']:
            return f"Ambas Marcam Sim @ {outcome.get('price')}"

    return 'N/A'


def format_correct_score(market):
    """Formata placares corretos com odd abaixo de 10."""
    if not market:
        return 'N/A'

    scores = []
    for outcome in market.get('outcomes', []):
        name = outcome.get('name', 'N/A')
        price = outcome.get('price')
        try:
            if price is not None and float(price) < 10:
                scores.append(f'{name} @ {price}')
        except (ValueError, TypeError):
            continue

    if not scores:
        return 'Nenhum placar abaixo de 10'

    return '\n'.join(scores)


def format_datetime(iso_str):
    """Converte data ISO para formato legível."""
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        return dt.strftime('%d/%m/%Y %H:%M UTC')
    except Exception:
        return iso_str


def build_message(sport_name, event, odds_data):
    """Monta a mensagem formatada para o Telegram."""
    bookmaker = select_bookmaker(odds_data)
    markets = extract_markets(bookmaker)

    bm_name = bookmaker.get('title', 'N/A') if bookmaker else 'N/A'
    last_update = bookmaker.get('last_update', 'N/A') if bookmaker else 'N/A'

    h2h = format_h2h(markets.get('h2h'))
    totals_h1 = format_totals_h1(markets.get('totals_h1'))
    totals = format_totals(markets.get('totals'))
    btts = format_btts(markets.get('btts'))
    correct_score = format_correct_score(markets.get('correct_score'))

    home = event.get('home_team', 'N/A')
    away = event.get('away_team', 'N/A')
    commence = format_datetime(event.get('commence_time', 'N/A'))
    event_id = event.get('id', 'N/A')

    message = f"""🏆 <b>{sport_name}</b>
⚽ <b>{home} vs {away}</b>
🕒 Início: {commence}
🆔 Evento: {event_id}
📊 Bookmaker: {bm_name}
🔄 Última atualização: {last_update}

<b>Resultado da Partida (1X2):</b>
{h2h}

<b>Over 0.5 HT:</b>
{totals_h1}

<b>Over 2.5 FT:</b>
{totals}

<b>Ambas Marcam Sim:</b>
{btts}

<b>Placares Corretos (odd < 10):</b>
{correct_score}

—"""

    return message


def send_telegram_message(message):
    """Envia mensagem para o Telegram."""
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        logger.info('Mensagem enviada para o Telegram com sucesso')
        return True
    except Exception as e:
        logger.error(f'Erro ao enviar mensagem para Telegram: {e}')
        return False


def main():
    """Fluxo principal do script."""
    logger.info('Iniciando analise_odds ultra-avançado')

    for sport_key, sport_name in SPORTS.items():
        events = get_events(sport_key)

        for event in events:
            event_id = event.get('id')
            if not event_id:
                logger.warning('Evento sem ID, ignorando')
                continue

            odds_data = get_event_odds(sport_key, event_id)
            if not odds_data:
                logger.warning(f'Nenhuma odd retornada para {event_id}')
                continue

            message = build_message(sport_name, event, odds_data)
            send_telegram_message(message)

            # Respeita rate limit entre chamadas
            time.sleep(1.0 / CALLS_PER_SECOND)

    logger.info('Analise_odds finalizado')


if __name__ == '__main__':
    main()
