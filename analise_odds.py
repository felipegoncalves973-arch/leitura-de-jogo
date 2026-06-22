import os
import sys
import time
import json
import requests
import html
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Any

# =============================================================================
# CONFIGURAÇÃO E VARIÁVEIS DE AMBIENTE
# =============================================================================

API_KEY = os.environ.get("ODDS_API_KEY", "SUA_ODDS_API_KEY_AQUI")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "SEU_BOT_TOKEN_AQUI")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "SEU_CHAT_ID_AQUI")

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

REGIONS = ["uk", "eu", "us", "au"]
COMPETITIONS = {
    "fifa_world_cup": {"sport": "soccer", "name": "Copa do Mundo"},
    "brazil_serie_b": {"sport": "soccer", "name": "Série B"},
}

WINDOW_HOURS = 24
MARKETS = ["h2h", "btts", "totals", "correct_score", "totals_h1"]
MIN_CORRECT_SCORE_ODD = 10.0
MAX_CORRECT_SCORE_ODD = 999.0
REQUEST_TIMEOUT = 30
SLEEP_BETWEEN_EVENTS = 0.5
SLEEP_BETWEEN_MARKETS = 0.2
SLEEP_BETWEEN_RETRIES = 2
MAX_RETRIES = 3

BETFAIR_EXCHANGE_KEY = "betfairexchange"
BETFAIR_SPORTSBOOK_KEY = "betfair"

# =============================================================================
# UTILITÁRIOS
# =============================================================================

def log(message: str, level: str = "INFO") -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def safe_get(url: str, params: Dict[str, Any], retries: int = MAX_RETRIES) -> Optional[Dict]:
    attempt = 0
    while attempt <= retries:
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.json()
            if response.status_code in (429, 500, 502, 503, 504):
                log(f"HTTP {response.status_code} em {url}. Retentativa {attempt + 1}/{retries + 1}", "WARN")
                time.sleep(SLEEP_BETWEEN_RETRIES * (attempt + 1))
            else:
                log(f"HTTP {response.status_code} em {url}: {response.text}", "ERROR")
                return None
        except requests.exceptions.RequestException as e:
            log(f"Erro de requisição em {url}: {e}. Retentativa {attempt + 1}/{retries + 1}", "WARN")
            time.sleep(SLEEP_BETWEEN_RETRIES * (attempt + 1))
        attempt += 1
    return None


def format_datetime(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        dt_local = dt.astimezone()
        return dt_local.strftime("%d/%m %H:%M")
    except Exception:
        return iso_str


# =============================================================================
# TELEGRAM VIA REQUESTS (HTML)
# =============================================================================

def send_telegram_html(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Token ou chat_id do Telegram não configurados. Mensagem não enviada.", "WARN")
        return False

    url = f"{TELEGRAM_API_BASE}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message[:4096],  # Limite do Telegram
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            return True
        log(f"Falha ao enviar mensagem Telegram: {response.status_code} - {response.text}", "ERROR")
        return False
    except requests.exceptions.RequestException as e:
        log(f"Erro ao enviar mensagem Telegram: {e}", "ERROR")
        return False


def escape_telegram_html(text: str) -> str:
    return html.escape(str(text))


# =============================================================================
# ODDSCHECKER
# =============================================================================

def fetch_events(competition_key: str) -> List[Dict]:
    config = COMPETITIONS.get(competition_key)
    if not config:
        return []

    sport = config["sport"]
    url = f"{ODDS_API_BASE}/sports/{sport}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": ",".join(REGIONS),
        "markets": ",".join(MARKETS),
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }

    data = safe_get(url, params)
    if not isinstance(data, list):
        log(f"Resposta inesperada para eventos de {competition_key}", "ERROR")
        return []

    events = []
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=WINDOW_HOURS)
    for event in data:
        try:
            commence = datetime.fromisoformat(event.get("commence_time", "").replace("Z", "+00:00"))
            if not (now <= commence <= cutoff):
                continue
            if competition_key.lower() in event.get("sport_key", "").lower():
                events.append(event)
        except Exception as e:
            log(f"Erro ao filtrar evento: {e}", "WARN")
    return events


def fetch_event_markets(event_id: str, sport_key: str, market: str) -> Optional[Dict]:
    url = f"{ODDS_API_BASE}/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": ",".join(REGIONS),
        "markets": market,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    return safe_get(url, params)


# =============================================================================
# ANÁLISE DE MERCADOS
# =============================================================================

def extract_bookmaker_odds(event: Dict, bookmaker_key: str, market: str) -> Dict[str, float]:
    odds_map = {}
    bookmakers = event.get("bookmakers", [])
    for bm in bookmakers:
        if bm.get("key") != bookmaker_key:
            continue
        for market_data in bm.get("markets", []):
            if market_data.get("key") != market:
                continue
            for outcome in market_data.get("outcomes", []):
                name = outcome.get("name")
                price = outcome.get("price")
                point = outcome.get("point")
                key = f"{name} ({point})" if point is not None else name
                try:
                    odds_map[key] = float(price)
                except (TypeError, ValueError):
                    continue
    return odds_map


def get_best_odds(event: Dict, market: str) -> List[Tuple[str, float, str]]:
    best = []
    best_by_outcome: Dict[str, Tuple[float, str]] = {}
    for bm in event.get("bookmakers", []):
        bm_key = bm.get("key", "unknown")
        for market_data in bm.get("markets", []):
            if market_data.get("key") != market:
                continue
            for outcome in market_data.get("outcomes", []):
                name = outcome.get("name")
                price = outcome.get("price")
                point = outcome.get("point")
                key = f"{name} ({point})" if point is not None else name
                try:
                    price_f = float(price)
                except (TypeError, ValueError):
                    continue
                if key not in best_by_outcome or price_f > best_by_outcome[key][0]:
                    best_by_outcome[key] = (price_f, bm_key)
    for key, (price, bm_key) in best_by_outcome.items():
        best.append((key, price, bm_key))
    best.sort(key=lambda x: x[1], reverse=True)
    return best


def analyze_correct_score(event: Dict) -> Dict[str, Any]:
    result = {
        "available": False,
        "exchange_available": False,
        "exchange_odds": {},
        "fallback_available": False,
        "fallback_odds": {},
        "filtered_scores": [],
    }
    exchange_odds = extract_bookmaker_odds(event, BETFAIR_EXCHANGE_KEY, "correct_score")
    if exchange_odds:
        result["available"] = True
        result["exchange_available"] = True
        result["exchange_odds"] = exchange_odds
    else:
        fallback_odds = extract_bookmaker_odds(event, BETFAIR_SPORTSBOOK_KEY, "correct_score")
        if fallback_odds:
            result["available"] = True
            result["fallback_available"] = True
            result["fallback_odds"] = fallback_odds

    source = result["exchange_odds"] if result["exchange_available"] else result["fallback_odds"]
    for score, price in source.items():
        if MIN_CORRECT_SCORE_ODD <= price <= MAX_CORRECT_SCORE_ODD:
            result["filtered_scores"].append((score, price))
    result["filtered_scores"].sort(key=lambda x: x[1])
    return result


# =============================================================================
# MENSAGENS
# =============================================================================

def build_event_summary(event: Dict, competition_name: str) -> str:
    home = escape_telegram_html(event.get("home_team", "N/A"))
    away = escape_telegram_html(event.get("away_team", "N/A"))
    commence = escape_telegram_html(format_datetime(event.get("commence_time", "")))
    msg = f"<<b>⚽ {escape_telegram_html(competition_name)}</b>\n"
    msg += f"<<b>{home}</b> vs <b>{away}</b>\n"
    msg += f"🕒 Início: <code>{commence}</code>\n"
    return msg


def build_market_section(title: str, best: List[Tuple[str, float, str]]) -> str:
    if not best:
        return f"\n<i>{title}:</i> <code>sem dados</code>\n"
    lines = [f"\n<i>{title}:</i>"]
    for outcome, price, bm_key in best[:5]:
        lines.append(f"  • {escape_telegram_html(outcome)}: <b>{price:.2f}</b> @ {escape_telegram_html(bm_key)}")
    return "\n".join(lines) + "\n"


def build_correct_score_section(cs_analysis: Dict) -> str:
    if not cs_analysis["available"]:
        return "\n<i>Placar Correto:</i> <code>indisponível na Betfair</code>\n"

    source_label = "Betfair Exchange" if cs_analysis["exchange_available"] else "Betfair (fallback)"
    lines = [f"\n<i>Placar Correto ({escape_telegram_html(source_label)}):</i>"]
    if not cs_analysis["filtered_scores"]:
        lines.append("  <code>nenhum placar abaixo de 10.00</code>")
    else:
        for score, price in cs_analysis["filtered_scores"]:
            lines.append(f"  • {escape_telegram_html(score)}: <b>{price:.2f}</b>")
    return "\n".join(lines) + "\n"


# =============================================================================
# ORQUESTRAÇÃO
# =============================================================================

def analyze_competition(competition_key: str) -> None:
    config = COMPETITIONS.get(competition_key)
    if not config:
        return

    log(f"Iniciando análise: {config['name']}")
    events = fetch_events(competition_key)
    log(f"{len(events)} eventos encontrados nas próximas {WINDOW_HOURS}h para {config['name']}")

    for event in events:
        event_id = event.get("id")
        sport_key = event.get("sport_key")
        if not event_id or not sport_key:
            continue

        try:
            h2h_best = get_best_odds(event, "h2h")
            btts_best = get_best_odds(event, "btts")
            totals_best = get_best_odds(event, "totals")
            totals_h1_best = get_best_odds(event, "totals_h1")
            cs_analysis = analyze_correct_score(event)

            # Caso os mercados individuais não estejam populados, tenta buscar individualmente
            for market in MARKETS:
                if market == "h2h" and not h2h_best:
                    detail = fetch_event_markets(event_id, sport_key, market)
                    if detail:
                        h2h_best = get_best_odds(detail, market)
                elif market == "btts" and not btts_best:
                    detail = fetch_event_markets(event_id, sport_key, market)
                    if detail:
                        btts_best = get_best_odds(detail, market)
                elif market == "totals" and not totals_best:
                    detail = fetch_event_markets(event_id, sport_key, market)
                    if detail:
                        totals_best = get_best_odds(detail, market)
                elif market == "totals_h1" and not totals_h1_best:
                    detail = fetch_event_markets(event_id, sport_key, market)
                    if detail:
                        totals_h1_best = get_best_odds(detail, market)
                elif market == "correct_score" and not cs_analysis["available"]:
                    detail = fetch_event_markets(event_id, sport_key, market)
                    if detail:
                        cs_analysis = analyze_correct_score(detail)
                time.sleep(SLEEP_BETWEEN_MARKETS)

            msg = build_event_summary(event, config["name"])
            msg += build_market_section("Moneyline (H2H)", h2h_best)
            msg += build_market_section("Ambas Marcam (BTTS)", btts_best)
            msg += build_market_section("Total de Gols (Over/Under)", totals_best)
            msg += build_market_section("Total Gols 1º Tempo (H1)", totals_h1_best)
            msg += build_correct_score_section(cs_analysis)
            msg += "\n─────────────"

            if not send_telegram_html(msg):
                log("Mensagem não enviada ao Telegram. Imprimindo localmente.", "WARN")
                print(msg)

        except Exception as e:
            log(f"Erro inesperado ao analisar evento {event_id}: {e}", "ERROR")

        time.sleep(SLEEP_BETWEEN_EVENTS)


def main() -> None:
    if API_KEY == "SUA_ODDS_API_KEY_AQUI":
        log("ODDS_API_KEY não configurada. Defina a variável de ambiente.", "ERROR")
        sys.exit(1)
    if TELEGRAM_BOT_TOKEN == "SEU_BOT_TOKEN_AQUI" or TELEGRAM_CHAT_ID == "SEU_CHAT_ID_AQUI":
        log("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurados. As mensagens serão impressas localmente.", "WARN")

    for competition_key in COMPETITIONS:
        try:
            analyze_competition(competition_key)
        except Exception as e:
            log(f"Erro crítico na competição {competition_key}: {e}", "ERROR")

    log("Análise finalizada.")


if __name__ == "__main__":
    main()
