mport os
import sys
import math
import json
import time
import requests
import csv
from datetime import datetime, timedelta
from collections import defaultdict

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================
API_KEY = os.getenv("ODDS_API_KEY", "SUA_API_KEY_AQUI")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

REGION = "eu"
ODDS_FORMAT = "decimal"
DATE_FORMAT = "iso"
SPORTS = {
    "FIFA World Cup": "soccer_fifa_world_cup",
    "Brasileirão Série B": "soccer_brazil_campeonate",
}
MARKETS = [
    "h2h",          # 1X2
    "totals",       # Over/Under
    "both_teams_to_score",
    "draw_no_bet",
    "double_chance",
]
CSV_FILENAME = "oportunidades.csv"
BOOKMAKERS_PREFERENCES = ["betfair_exchange", "betfair"]

TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def log(message: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {message}")


def format_iso(dt: datetime) -> str:
    """Retorna string ISO UTC com 'Z'."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def telegram_message(text: str) -> None:
    if not TELEGRAM_ENABLED:
        log("Telegram desabilitado: token/chat_id não configurados.", "WARNING")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        log("Mensagem enviada para o Telegram.")
    except requests.RequestException as exc:
        log(f"Falha ao enviar Telegram: {exc}", "ERROR")


# =============================================================================
# API THE ODDS
# =============================================================================
BASE_URL = "https://api.the-odds-api.com/v4"


def fetch_events(sport_key: str, start: datetime, end: datetime) -> list:
    url = f"{BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": REGION,
        "markets": ",".join(MARKETS),
        "oddsFormat": ODDS_FORMAT,
        "dateFormat": DATE_FORMAT,
        "commenceTimeFrom": format_iso(start),
        "commenceTimeTo": format_iso(end),
        "bookmakers": ",".join(BOOKMAKERS_PREFERENCES),
    }
    log(f"Buscando eventos: {sport_key} de {start} até {end}")
    try:
        response = requests.get(url, params=params, timeout=60)
        if response.status_code == 422:
            log(f"Erro 422 para {sport_key}: {response.text}", "ERROR")
            return []
        response.raise_for_status()
        data = response.json()
        log(f"{len(data)} eventos encontrados para {sport_key}.")
        return data
    except requests.RequestException as exc:
        log(f"Erro na requisição para {sport_key}: {exc}", "ERROR")
        return []


def pick_bookmaker(odds_data: dict) -> dict:
    """Escolhe o bookmaker preferido, com fallback."""
    bookmakers = odds_data.get("bookmakers", [])
    if not bookmakers:
        return {}

    for pref in BOOKMAKERS_PREFERENCES:
        for bm in bookmakers:
            if bm.get("key") == pref:
                return bm

    return bookmakers[0]


def extract_market_odds(bm: dict, market_key: str) -> dict:
    for market in bm.get("markets", []):
        if market.get("key") == market_key:
            return market
    return {}


def normalize_outcome_name(name: str, market_key: str) -> str:
    name = str(name).strip().lower()
    if market_key == "h2h":
        return {"home": "1", "away": "2", "draw": "x"}.get(name, name)
    if market_key == "totals":
        if "over" in name:
            return "over"
        if "under" in name:
            return "under"
    return name


def get_market_prices(odds_data: dict, market_key: str) -> dict:
    """Retorna dict {outcome_normalizado: odd} para o mercado escolhido."""
    bm = pick_bookmaker(odds_data)
    market = extract_market_odds(bm, market_key)
    result = {}
    for outcome in market.get("outcomes", []):
        key = normalize_outcome_name(outcome.get("name", ""), market_key)
        if market_key == "totals":
            point = outcome.get("point")
            if point is not None and float(point) == 2.5:
                result[key] = float(outcome.get("price", 0))
        else:
            result[key] = float(outcome.get("price", 0))
    return result


# =============================================================================
# POISSON / CORRECT SCORE
# =============================================================================
def implied_probability(odd: float) -> float:
    if odd <= 0:
        return 0.0
    return 1.0 / odd


def poisson_pmf(lambda_: float, k: int) -> float:
    if lambda_ <= 0 or k < 0:
        return 0.0
    return (math.exp(-lambda_) * (lambda_ ** k)) / math.factorial(k)


def estimate_correct_score(h2h: dict, totals: dict) -> list:
    """
    Estima placares corretos via Poisson calibrado pelas odds 1X2 e Over 2.5.
    Retorna lista de dicts com score, prob, odd, raw_prob_h2h, raw_prob_poisson.
    """
    home_odd = h2h.get("1", 0)
    draw_odd = h2h.get("x", 0)
    away_odd = h2h.get("2", 0)
    over_odd = totals.get("over", 0)

    if not all([home_odd, draw_odd, away_odd, over_odd]):
        return []

    p_home = implied_probability(home_odd)
    p_draw = implied_probability(draw_odd)
    p_away = implied_probability(away_odd)
    p_over = implied_probability(over_odd)

    # Margem bruta de mercado: normalização simples para somar 1
    total = p_home + p_draw + p_away
    if total <= 0:
        return []
    p_home /= total
    p_draw /= total
    p_away /= total

    # Estimativa inicial de lambdas via relação home/draw/away
    # lambda_home e lambda_away positivos com empate representado por proximidade de gols.
    # Usamos heurística: a soma esperada de gols E[g] é inferida pelo mercado Over 2.5.
    # Se p_over ~ 0.50, threshold Over 2.5 => E[g] próximo de 2.5 para odds justas.
    # Aproximamos E[g] = 2.5 * p_over / (1 - p_over)  (odds ratio simplificado).
    if p_over >= 1.0:
        expected_goals = 3.0
    else:
        expected_goals = 2.5 * (p_over / (1.0 - p_over))

    expected_goals = max(1.0, min(expected_goals, 5.0))

    # lambda_home + lambda_away = expected_goals
    # lambda_home / lambda_away = p_home / p_away (heurística simples)
    if p_away <= 0:
        lambda_home = expected_goals
        lambda_away = 0.1
    else:
        ratio = p_home / p_away
        lambda_home = expected_goals * ratio / (1.0 + ratio)
        lambda_away = expected_goals / (1.0 + ratio)

    lambda_home = max(0.1, lambda_home)
    lambda_away = max(0.1, lambda_away)

    # Calibrar lambdas para ajustar a probabilidade empírica de empate
    # Queremos que P(empate) ~= p_draw. Ajustamos ambos lambdas via fator de calibração.
    max_goals = 6
    raw_draw = 0.0
    for g in range(max_goals + 1):
        raw_draw += poisson_pmf(lambda_home, g) * poisson_pmf(lambda_away, g)

    if raw_draw > 0 and p_draw > 0:
        calibration = math.log(p_draw) / math.log(raw_draw)
    else:
        calibration = 1.0

    calibration = max(0.5, min(2.0, calibration))

    lambda_home_cal = lambda_home ** calibration
    lambda_away_cal = lambda_away ** calibration
    lambda_home_cal = max(0.1, lambda_home_cal)
    lambda_away_cal = max(0.1, lambda_away_cal)

    scores = []
    for gh in range(max_goals + 1):
        for ga in range(max_goals + 1):
            prob = poisson_pmf(lambda_home_cal, gh) * poisson_pmf(lambda_away_cal, ga)
            odd = 1.0 / prob if prob > 0 else 0.0
            scores.append({
                "score": f"{gh}x{ga}",
                "prob": prob,
                "odd": odd,
                "raw_prob_h2h": None,
                "raw_prob_poisson": prob,
            })

    # Normalizar probabilidades para somar 1
    total_prob = sum(s["prob"] for s in scores)
    if total_prob > 0:
        for s in scores:
            s["prob"] /= total_prob
            s["odd"] = 1.0 / s["prob"] if s["prob"] > 0 else 0.0

    return scores


# =============================================================================
# ANÁLISE / FILTROS
# =============================================================================
def analyze_event(event: dict, sport_name: str) -> dict:
    home_team = event.get("home_team", "Home")
    away_team = event.get("away_team", "Away")
    commence_time = event.get("commence_time", "")
    bookmaker_key = ""

    bm = pick_bookmaker(event)
    if bm:
        bookmaker_key = bm.get("title", bm.get("key", "desconhecido"))

    h2h = get_market_prices(event, "h2h")
    totals = get_market_prices(event, "totals")
    btts = get_market_prices(event, "both_teams_to_score")

    correct_scores = estimate_correct_score(h2h, totals)
    filtered_scores = [s for s in correct_scores if 0 < s["odd"] < 10.0]
    filtered_scores.sort(key=lambda x: x["odd"])

    return {
        "sport": sport_name,
        "home_team": home_team,
        "away_team": away_team,
        "commence_time": commence_time,
        "bookmaker": bookmaker_key,
        "h2h": h2h,
        "totals": totals,
        "btts": btts,
        "correct_scores": filtered_scores,
    }


def find_value_opportunities(analyzed: list) -> list:
    """Agrupa oportunidades para CSV e Telegram."""
    rows = []
    for item in analyzed:
        for score in item["correct_scores"]:
            rows.append({
                "sport": item["sport"],
                "home_team": item["home_team"],
                "away_team": item["away_team"],
                "commence_time": item["commence_time"],
                "bookmaker": item["bookmaker"],
                "market": "correct_score",
                "selection": score["score"],
                "odd": round(score["odd"], 2),
                "estimated_probability": round(score["prob"] * 100, 2),
            })
    return rows


# =============================================================================
# CSV NATIVO
# =============================================================================
def write_csv(rows: list, filename: str) -> None:
    fieldnames = [
        "sport",
        "home_team",
        "away_team",
        "commence_time",
        "bookmaker",
        "market",
        "selection",
        "odd",
        "estimated_probability",
    ]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    log(f"Arquivo CSV gerado: {filename} ({len(rows)} linhas).")


# =============================================================================
# HTML TELEGRAM
# =============================================================================
def build_html_message(analyzed: list) -> str:
    if not analyzed:
        return "<<b>Nenhuma oportunidade encontrada nas próximas 24h.</b>"

    lines = []
    lines.append("<<b>⚽ Oportunidades de Odds - Copa do Mundo & Série B</b>\n")
    lines.append(f"<<i>Atualizado: {datetime.now().strftime('%d/%m %H:%M')}</i>\n")

    for item in analyzed:
        if not item["correct_scores"]:
            continue

        time_str = "N/A"
        try:
            dt = datetime.fromisoformat(item["commence_time"].replace("Z", "+00:00"))
            time_str = dt.strftime("%d/%m %H:%M")
        except Exception:
            time_str = item["commence_time"]

        lines.append(
            f"<<b>{item['home_team']}</b> x <b>{item['away_team']}</b> "
            f"({item['sport']}) - {time_str}"
        )
        lines.append(f"Bookmaker: {item['bookmaker']}")
        h2h_text = " | ".join(f"{k}: {v}" for k, v in item["h2h"].items())
        totals_text = " | ".join(f"{k}: {v}" for k, v in item["totals"].items())
        lines.append(f"1X2: {h2h_text} | OU2.5: {totals_text}")
        lines.append("Placares estimados (odd < 10):")
        for score in item["correct_scores"][:6]:  # limita os 6 melhores
            lines.append(
                f"  {score['score']} @ {score['odd']:.2f} "
                f"(~{score['prob']*100:.1f}%)"
            )
        lines.append("")  # linha em branco

    return "\n".join(lines)


# =============================================================================
# MAIN
# =============================================================================
def main() -> int:
    if API_KEY in ("", "SUA_API_KEY_AQUI"):
        log("API Key não configurada. Defina ODDS_API_KEY.", "ERROR")
        return 1

    now = datetime.utcnow()
    start = now
    end = now + timedelta(hours=24)

    all_analyzed = []
    for sport_name, sport_key in SPORTS.items():
        events = fetch_events(sport_key, start, end)
        if not events:
            continue
        for event in events:
            analyzed = analyze_event(event, sport_name)
            if analyzed["correct_scores"]:
                all_analyzed.append(analyzed)
        time.sleep(1)

    opportunities = find_value_opportunities(all_analyzed)
    write_csv(opportunities, CSV_FILENAME)

    message = build_html_message(all_analyzed)
    telegram_message(message)

    log("Análise concluída com sucesso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
