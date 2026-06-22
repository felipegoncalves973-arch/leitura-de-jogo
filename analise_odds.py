import os
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Any

# =============================================================================
# CONFIGURAÇÃO E CONSTANTES
# =============================================================================

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4/sports"
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

COMPETITIONS = {
    "soccer_fifa_world_cup": "Copa do Mundo FIFA",
    "soccer_brazil_serie_b": "Brasileirão Série B",
}

BOOKMAKER_PRIORITY = ["betfair_ex", "betfair"]

MARKETS = ["h2h", "btts", "totals", "correct_score", "totals_h1"]

TELEGRAM_MAX_MESSAGE_LENGTH = 4000


# =============================================================================
# EXCEÇÕES PERSONALIZADAS
# =============================================================================

class OddsApiError(Exception):
    pass


class TelegramError(Exception):
    pass


# =============================================================================
# FUNÇÕES DE APOIO
# =============================================================================

def iso_now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_plus_24h_utc() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_datetime_br(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(timezone.utc)
        return dt.strftime("%d/%m/%Y às %H:%M") + " (UTC)"
    except Exception:
        return iso_str


def get_betfair_odds(bookmakers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for bm in bookmakers:
        if bm.get("key") in BOOKMAKER_PRIORITY:
            return bm
    for bm in bookmakers:
        if "betfair" in bm.get("key", "").lower():
            return bm
    return None


def find_outcome(market_outcomes: List[Dict[str, Any]], name: str) -> Optional[str]:
    for outcome in market_outcomes:
        if outcome.get("name", "").strip().lower() == name.strip().lower():
            return str(outcome.get("price"))
    return None


def h2h_lines(bookmaker: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not bookmaker:
        return {"casa": "—", "empate": "—", "fora": "—"}
    outcomes = bookmaker.get("markets", [{}])[0].get("outcomes", []) if bookmaker.get("markets") else []
    if len(outcomes) >= 3:
        return {
            "casa": str(outcomes[0].get("price", "—")),
            "empate": str(outcomes[1].get("price", "—")),
            "fora": str(outcomes[2].get("price", "—")),
        }
    return {
        "casa": find_outcome(outcomes, "Home") or "—",
        "empate": find_outcome(outcomes, "Draw") or "—",
        "fora": find_outcome(outcomes, "Away") or "—",
    }


def btts_lines(bookmaker: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not bookmaker:
        return {"sim": "—", "nao": "—"}
    outcomes = bookmaker.get("markets", [{}])[0].get("outcomes", []) if bookmaker.get("markets") else []
    return {
        "sim": find_outcome(outcomes, "Yes") or "—",
        "nao": find_outcome(outcomes, "No") or "—",
    }


def totals_lines(bookmaker: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not bookmaker:
        return {"over_2_5": "—", "under_2_5": "—"}
    outcomes = bookmaker.get("markets", [{}])[0].get("outcomes", []) if bookmaker.get("markets") else []
    over = find_outcome(outcomes, "Over") or find_outcome(outcomes, "Over 2.5") or "—"
    under = find_outcome(outcomes, "Under") or find_outcome(outcomes, "Under 2.5") or "—"
    point = None
    for outcome in outcomes:
        if outcome.get("point") is not None:
            point = outcome.get("point")
            break
    return {"over_2_5": over, "under_2_5": under, "point": point}


def totals_h1_lines(bookmaker: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not bookmaker:
        return {"over_0_5": "—", "under_0_5": "—"}
    outcomes = bookmaker.get("markets", [{}])[0].get("outcomes", []) if bookmaker.get("markets") else []
    over = find_outcome(outcomes, "Over") or "—"
    under = find_outcome(outcomes, "Under") or "—"
    point = None
    for outcome in outcomes:
        if outcome.get("point") is not None:
            point = outcome.get("point")
            break
    return {"over": over, "under": under, "point": point}


def correct_score_lines(bookmaker: Optional[Dict[str, Any]]) -> List[str]:
    if not bookmaker:
        return []
    outcomes = bookmaker.get("markets", [{}])[0].get("outcomes", []) if bookmaker.get("markets") else []
    sorted_outcomes = sorted(outcomes, key=lambda x: float(x.get("price", 1)))[:5]
    return [f"{o.get('name')} @ {o.get('price')}" for o in sorted_outcomes]


# =============================================================================
# INTEGRAÇÃO COM A ODDS API
# =============================================================================

def fetch_events(sport: str) -> List[Dict[str, Any]]:
    if not ODDS_API_KEY:
        raise OddsApiError("A variável de ambiente ODDS_API_KEY não está configurada.")

    url = f"{ODDS_API_BASE_URL}/{sport}/events"
    params = {
        "apiKey": ODDS_API_KEY,
        "commenceTimeFrom": iso_now_utc(),
        "commenceTimeTo": iso_plus_24h_utc(),
    }
    response = requests.get(url, params=params, timeout=30)
    if response.status_code != 200:
        raise OddsApiError(f"Erro ao buscar eventos para {sport}: {response.status_code} - {response.text}")
    return response.json()


def fetch_event_odds(sport: str, event_id: str, market: str) -> Optional[Dict[str, Any]]:
    if not ODDS_API_KEY:
        raise OddsApiError("A variável de ambiente ODDS_API_KEY não está configurada.")

    url = f"{ODDS_API_BASE_URL}/{sport}/events/{event_id}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": market,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    try:
        response = requests.get(url, params=params, timeout=30)
    except requests.RequestException as exc:
        print(f"[AVISO] Falha de conexão no mercado {market} do evento {event_id}: {exc}")
        return None

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        print(f"[AVISO] Erro ao buscar odds {market} do evento {event_id}: {response.status_code} - {response.text}")
        return None

    return response.json()


# =============================================================================
# INTEGRAÇÃO COM O TELEGRAM
# =============================================================================

def send_telegram_html(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise TelegramError("As variáveis TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID devem estar configuradas.")

    url = TELEGRAM_API_URL.format(token=TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message[:TELEGRAM_MAX_MESSAGE_LENGTH],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    response = requests.post(url, json=payload, timeout=30)
    if response.status_code != 200:
        raise TelegramError(f"Erro ao enviar mensagem para o Telegram: {response.status_code} - {response.text}")
    return response.json().get("ok", False)


def send_no_games_message() -> bool:
    message = (
        "🤖 <b>Robô de Análise de Odds</b> está ativo e monitorando os mercados.\n\n"
        "⚽ Não encontrei nenhum jogo agendado para as próximas 24 horas nas competições selecionadas.\n\n"
        "🔍 Assim que houver partidas disponíveis, enviarei a análise completa com as odds da Betfair."
    )
    return send_telegram_html(message)


# =============================================================================
# FORMATAÇÃO HTML
# =============================================================================

def build_html_message(event: Dict[str, Any], sport_title: str, market_data: Dict[str, Optional[Dict[str, Any]]]) -> str:
    home = event.get("home_team", "Time da Casa")
    away = event.get("away_team", "Time Visitante")
    commence = format_datetime_br(event.get("commence_time", iso_now_utc()))

    h2h = h2h_lines(get_betfair_odds(market_data.get("h2h", {}).get("bookmakers", [])))
    btts = btts_lines(get_betfair_odds(market_data.get("btts", {}).get("bookmakers", [])))
    totals = totals_lines(get_betfair_odds(market_data.get("totals", {}).get("bookmakers", [])))
    correct_score = correct_score_lines(get_betfair_odds(market_data.get("correct_score", {}).get("bookmakers", [])))
    totals_h1 = totals_h1_lines(get_betfair_odds(market_data.get("totals_h1", {}).get("bookmakers", [])))

    def li(line: str) -> str:
        return f"• {line}\n"

    cs_block = ""
    if correct_score:
        cs_block = "\n🏁 <b>Placar Exato:</b>\n" + "".join(li(score) for score in correct_score)

    totals_point = f" {totals.get('point')}" if totals.get("point") is not None else " 2.5"
    totals_h1_point = f" {totals_h1.get('point')}" if totals_h1.get("point") is not None else ""

    message = f"""
⚽ <b>{sport_title}</b>
🏠 <b>{home}</b> vs 🛫 <b>{away}</b>
📅 <b>Início:</b> {commence}

🎯 <b>1X2 — Match Odds:</b>
{li(f"Casa: {h2h['casa']}")}{li(f"Empate: {h2h['empate']}")}{li(f"Fora: {h2h['fora']}")}
🤝 <b>Ambas Marcam:</b>
{li(f"Sim: {btts['sim']}")}{li(f"Não: {btts['nao']}")}
📊 <b>Gols — Total{totals_point}:</b>
{li(f"Over: {totals['over_2_5']}")}{li(f"Under: {totals['under_2_5']}")}
⏱️ <b>Gols 1º Tempo — Total{totals_h1_point}:</b>
{li(f"Over: {totals_h1['over']}")}{li(f"Under: {totals_h1['under']}")}{cs_block}
💰 <b>Odds extraídas da Betfair Exchange</b> (fallback: Betfair Sportsbook)
""".strip()

    return message


# =============================================================================
# ORQUESTRAÇÃO PRINCIPAL
# =============================================================================

def analyze_sport(sport_key: str, sport_title: str) -> None:
    print(f"[INFO] Buscando eventos para: {sport_title} ({sport_key})")
    events = fetch_events(sport_key)
    print(f"[INFO] {len(events)} evento(s) encontrado(s) para {sport_title}")

    if not events:
        return

    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue

        market_data: Dict[str, Optional[Dict[str, Any]]] = {}
        for market in MARKETS:
            try:
                odds = fetch_event_odds(sport_key, event_id, market)
                market_data[market] = odds
            except OddsApiError as exc:
                print(f"[ERRO] {exc}")
                market_data[market] = None

        message = build_html_message(event, sport_title, market_data)
        try:
            send_telegram_html(message)
            print(f"[INFO] Mensagem enviada para: {event.get('home_team')} x {event.get('away_team')}")
        except TelegramError as exc:
            print(f"[ERRO] {exc}")


def main() -> None:
    total_events = 0

    for sport_key, sport_title in COMPETITIONS.items():
        try:
            events = fetch_events(sport_key)
            total_events += len(events)
        except OddsApiError as exc:
            print(f"[ERRO] {exc}")
            continue

    if total_events == 0:
        print("[INFO] Nenhum jogo encontrado nas próximas 24 horas. Enviando mensagem informativa.")
        try:
            send_no_games_message()
        except TelegramError as exc:
            print(f"[ERRO] {exc}")
        return

    for sport_key, sport_title in COMPETITIONS.items():
        try:
            analyze_sport(sport_key, sport_title)
        except OddsApiError as exc:
            print(f"[ERRO] {exc}")
            continue


if __name__ == "__main__":
    main()
