import os
import csv
import json
import time
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ---------------------------------------------------------------------------
# Configurações
# ---------------------------------------------------------------------------
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Ligas monitoradas
SPORTS = {
    "soccer_fifa_world_cup": "Copa do Mundo",
    "soccer_brazil_serie_b": "Série B - Brasil",
}

# Mercados desejados e chaves para identificação
MARKET_TARGETS = {
    "h2h": ("Resultado Final", "h2h"),
    "totals": ("Over 2.5 FT", "over", "2.5"),
    "btts": ("Ambas Marcam Sim", "btts", "yes"),
    "totals_h1": ("Over 0.5 HT", "totals_h1", "over", "0.5"),
}

BOOKMAKERS = ["betfair_ex", "betfair"]
CSV_FILE = "oportunidades_odds.csv"


def parse_iso(iso_str):
    """Converte ISO 8601 para datetime aware em UTC."""
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None


def is_within_24h(commence_time):
    """Verifica se o evento começa dentro das próximas 24 horas."""
    if not commence_time:
        return False
    now = datetime.now(timezone.utc)
    return now <= commence_time <= now + timedelta(hours=24)


def fetch_events(sport_key):
    """Busca eventos da API-TheOdds para o esporte/liga."""
    url = (
        f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
        f"?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h,totals,btts,totals_h1&oddsFormat=decimal"
    )
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            print(f"[ERRO] {sport_key}: status {resp.status_code}")
            return []
        return resp.json()
    except Exception as e:
        print(f"[ERRO] {sport_key}: {e}")
        return []


def extract_best_bookmaker_odds(bookmakers, market_key):
    """Retorna as odds do primeiro bookmaker prioritário disponível para o mercado."""
    for bm in bookmakers or []:
        if bm.get("key") not in BOOKMAKERS:
            continue
        for market in bm.get("markets", []):
            if market.get("key") == market_key:
                return market.get("outcomes", [])
    return []


def pick_outcome_odds(outcomes, market_key):
    """Seleciona o valor correto de acordo com o mercado."""
    if not outcomes:
        return "N/A"

    if market_key == "h2h":
        # Retorna string Home / Draw / Away
        home = next((o for o in outcomes if o.get("name") == "Home"), None)
        draw = next((o for o in outcomes if o.get("name") == "Draw"), None)
        away = next((o for o in outcomes if o.get("name") == "Away"), None)
        h = home.get("price", "N/A") if home else "N/A"
        d = draw.get("price", "N/A") if draw else "N/A"
        a = away.get("price", "N/A") if away else "N/A"
        return f"H={h} | D={d} | A={a}"

    if market_key in ("totals", "totals_h1"):
        target = next((o for o in outcomes if o.get("name", "").lower() == "over" and o.get("point") == 2.5), None)
        if not target and market_key == "totals_h1":
            target = next((o for o in outcomes if o.get("name", "").lower() == "over" and o.get("point") == 0.5), None)
        return target.get("price", "N/A") if target else "N/A"

    if market_key == "btts":
        target = next((o for o in outcomes if o.get("name", "").lower() == "yes"), None)
        return target.get("price", "N/A") if target else "N/A"

    return "N/A"


def analyze_event(event, sport_key):
    """Extrai odds prioritárias de um evento."""
    commence_time = parse_iso(event.get("commence_time"))
    if not is_within_24h(commence_time):
        return None

    row = {
        "liga": SPORTS.get(sport_key, sport_key),
        "evento": event.get("home_team", "N/A") + " vs " + event.get("away_team", "N/A"),
        "inicio": commence_time.strftime("%Y-%m-%d %H:%M UTC") if commence_time else "N/A",
    }

    bookmakers = event.get("bookmakers", [])
    for market_key, (label, *_) in MARKET_TARGETS.items():
        outcomes = extract_best_bookmaker_odds(bookmakers, market_key)
        row[label] = pick_outcome_odds(outcomes, market_key)

    return row


def save_csv(rows, filepath):
    """Persiste as oportunidades no CSV usando apenas a biblioteca nativa csv."""
    if not rows:
        return False
    fieldnames = ["liga", "evento", "inicio", "Resultado Final", "Over 2.5 FT", "Ambas Marcam Sim", "Over 0.5 HT"]
    try:
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return True
    except Exception as e:
        print(f"[ERRO] CSV: {e}")
        return False


def build_html_message(rows):
    """Constrói mensagem HTML formatada para o Telegram."""
    if not rows:
        return "<<b>Nenhuma oportunidade encontrada nas próximas 24h.</b>"

    lines = ["<<b>Oportunidades de Odds - Próximas 24h</b>", ""]
    for row in rows:
        lines.append(f"<<b>{row['liga']}</b>")
        lines.append(f"⚽ {row['evento']}")
        lines.append(f"🕒 {row['inicio']}")
        lines.append(f"1X2: {row['Resultado Final']}")
        lines.append(f"Over 2.5 FT: {row['Over 2.5 FT']}")
        lines.append(f"Ambas Marcam Sim: {row['Ambas Marcam Sim']}")
        lines.append(f"Over 0.5 HT: {row['Over 0.5 HT']}")
        lines.append("")
    return "\n".join(lines)


def send_telegram(html_message):
    """Envia mensagem HTML para o Telegram via requests."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[AVISO] Telegram não configurado.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": html_message,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            print("[OK] Mensagem enviada ao Telegram.")
            return True
        else:
            print(f"[ERRO] Telegram: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        print(f"[ERRO] Telegram: {e}")
        return False


def main():
    if not ODDS_API_KEY:
        print("[ERRO] ODDS_API_KEY não configurada.")
        return

    all_rows = []
    for sport_key in SPORTS:
        print(f"[INFO] Buscando {sport_key}...")
        events = fetch_events(sport_key)
        for event in events or []:
            try:
                row = analyze_event(event, sport_key)
                if row:
                    all_rows.append(row)
            except Exception as e:
                print(f"[ERRO] Processando evento {event.get('id')}: {e}")

    print(f"[INFO] Total de oportunidades: {len(all_rows)}")

    if save_csv(all_rows, CSV_FILE):
        print(f"[OK] Salvo em {CSV_FILE}")

    message = build_html_message(all_rows)
    send_telegram(message)


if __name__ == "__main__":
    main()
