import os
import sys
import time
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

import requests


# ---------------------------------------------------------------------------
# Configuração de logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("analise_odds")


# ---------------------------------------------------------------------------
# Constantes e variáveis de ambiente
# ---------------------------------------------------------------------------
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

ODDS_BASE_URL = "https://api.the-odds-api.com/v4"
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

MAX_RETRIES = 3
BACKOFF_SECONDS = 2
TIMEOUT_SECONDS = 30


# ---------------------------------------------------------------------------
# Helpers de Telegram
# ---------------------------------------------------------------------------
def send_telegram_message(text: str) -> bool:
    """Envia uma mensagem de texto para o Telegram, retornando True em caso de sucesso."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Variáveis TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configuradas.")
        return False

    url = TELEGRAM_API_URL.format(token=TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(url, json=payload, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            logger.info("Mensagem enviada ao Telegram com sucesso.")
            return True
        except Exception as exc:
            logger.warning(
                "Falha ao enviar mensagem ao Telegram (tentativa %d/%d): %s",
                attempt,
                MAX_RETRIES,
                exc,
            )
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_SECONDS * attempt)

    return False


def send_telegram_error_log(error_text: str) -> None:
    """Envia um log compacto de erro ao Telegram para diagnóstico."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    truncated = (error_text[:3500] + "...") if len(error_text) > 3500 else error_text
    message = (
        f"⚠️ <b>Erro no analise_odds</b>\n"
        f"<<b>Horário:</b> {timestamp}\n"
        f"<<b>Detalhes:</b>\n<pre>{truncated}</pre>"
    )
    send_telegram_message(message)


# ---------------------------------------------------------------------------
# Helpers da API The Odds
# ---------------------------------------------------------------------------
def fetch_with_retries(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """Faz requisição GET com retry automático e tratamento de erro robusto."""
    params = params or {}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else 0
            try:
                body = exc.response.json() if exc.response is not None else {}
            except Exception:
                body = exc.response.text if exc.response is not None else ""

            logger.error(
                "Erro HTTP na requisição (tentativa %d/%d): %s %s - %s",
                attempt,
                MAX_RETRIES,
                url,
                status_code,
                body,
            )
            if attempt == MAX_RETRIES:
                raise
        except Exception as exc:
            logger.error(
                "Erro de conexão na requisição (tentativa %d/%d): %s - %s",
                attempt,
                MAX_RETRIES,
                url,
                exc,
            )
            if attempt == MAX_RETRIES:
                raise

        time.sleep(BACKOFF_SECONDS * attempt)

    raise RuntimeError("Excedido número máximo de tentativas de requisição.")


def get_active_soccer_league() -> Optional[str]:
    """Busca dinamicamente a primeira liga de futebol (soccer) ativa na Odds API."""
    if not ODDS_API_KEY:
        raise ValueError("A variável de ambiente ODDS_API_KEY não está definida.")

    url = f"{ODDS_BASE_URL}/sports"
    params = {"apiKey": ODDS_API_KEY, "all": "false"}

    sports = fetch_with_retries(url, params)
    if not isinstance(sports, list):
        raise TypeError(f"Resposta inesperada de /sports: {type(sports)}")

    for sport in sports:
        if not isinstance(sport, dict):
            continue
        key = sport.get("key", "")
        group = sport.get("group", "")
        active = sport.get("active", False)
        if active and isinstance(key, str) and key.startswith("soccer"):
            logger.info(
                "Liga de futebol ativa selecionada: %s (%s)",
                key,
                group,
            )
            return key

    return None


def get_upcoming_events(sport_key: str, hours_ahead: int = 24) -> List[Dict[str, Any]]:
    """Obtém eventos futuros de uma liga dentro de uma janela de horas."""
    if not ODDS_API_KEY:
        raise ValueError("A variável de ambiente ODDS_API_KEY não está definida.")

    now = datetime.now(timezone.utc)
    commence_time_from = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    commence_time_to = (now + timedelta(hours=hours_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ")

    url = f"{ODDS_BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
        "commenceTimeFrom": commence_time_from,
        "commenceTimeTo": commence_time_to,
    }

    logger.info(
        "Buscando eventos para '%s' entre %s e %s",
        sport_key,
        commence_time_from,
        commence_time_to,
    )
    return fetch_with_retries(url, params)


# ---------------------------------------------------------------------------
# Lógica de análise de odds (placeholder)
# ---------------------------------------------------------------------------
def analyze_event(event: Dict[str, Any]) -> Optional[str]:
    """Retorna uma mensagem formatada caso identifique oportunidade na partida."""
    home = event.get("home_team", "Home")
    away = event.get("away_team", "Away")
    commence_time = event.get("commence_time", "N/A")
    bookmakers = event.get("bookmakers", [])

    if not bookmakers:
        return None

    # Exemplo simples: encontra a melhor odd para cada time entre os bookmakers.
    best_home_odds: Optional[float] = None
    best_away_odds: Optional[float] = None
    best_draw_odds: Optional[float] = None

    for bm in bookmakers:
        for market in bm.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes", []):
                name = outcome.get("name")
                price = outcome.get("price")
                if name == home:
                    if best_home_odds is None or price < best_home_odds:
                        best_home_odds = price
                elif name == away:
                    if best_away_odds is None or price < best_away_odds:
                        best_away_odds = price
                elif name.lower() in ("draw", "empate", "tie"):
                    if best_draw_odds is None or price < best_draw_odds:
                        best_draw_odds = price

    if not best_home_odds or not best_away_odds:
        return None

    # Lógica de identificação de oportunidade: odds equilibradas (ambas < 2.5).
    if best_home_odds < 2.5 and best_away_odds < 2.5:
        return (
            f"⚽ <b>{home} x {away}</b>\n"
            f"<<b>Início:</b> {commence_time}\n"
            f"<<b>Melhores odds:</b> {home} {best_home_odds:.2f} | "
            f"Empate {best_draw_odds:.2f if best_draw_odds else 'N/A'} | "
            f"{away} {best_away_odds:.2f}"
        )

    return None


# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------
def main() -> None:
    try:
        if not ODDS_API_KEY:
            raise ValueError("ODDS_API_KEY não está definida.")
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            raise ValueError("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não estão definidas.")

        # 1) Buscar a primeira liga de futebol ativa dinamicamente.
        sport_key = get_active_soccer_league()
        if not sport_key:
            logger.warning("Nenhuma liga de futebol ativa encontrada na API.")
            send_telegram_message(
                "🤖 <b>Robô analise_odds ativo</b>\n"
                "Nenhuma liga de futebol ativa foi encontrada na The Odds API no momento.\n"
                "O robô continua funcionando e tentará novamente na próxima execução."
            )
            return

        # 2) Buscar próximos eventos.
        events = get_upcoming_events(sport_key, hours_ahead=24)
        if not events:
            logger.info("Nenhuma partida encontrada para as próximas horas.")
            send_telegram_message(
                "🤖 <b>Robô analise_odds ativo e funcionando</b>\n"
                f"Liga monitorada: <code>{sport_key}</code>\n"
                "No momento não há jogos agendados para as próximas horas.\n"
                "Assim que houver partidas, enviaremos as análises."
            )
            return

        # 3) Analisar eventos e enviar oportunidades.
        opportunities_sent = 0
        for event in events:
            try:
                message = analyze_event(event)
                if message:
                    send_telegram_message(message)
                    opportunities_sent += 1
            except Exception as exc:
                logger.error("Erro ao analisar evento %s: %s", event.get("id"), exc)

        logger.info("Análise concluída. Oportunidades enviadas: %d", opportunities_sent)

        if opportunities_sent == 0:
            send_telegram_message(
                "🤖 <b>Robô analise_odds ativo e funcionando</b>\n"
                f"Foram encontradas {len(events)} partida(s) na liga <code>{sport_key}</code>,\n"
                "mas nenhuma oportunidade de odds selecionada no momento."
            )

    except Exception as exc:
        logger.exception("Erro fatal na execução do analise_odds.")
        # 3) Tentar enviar log do erro para o Telegram para facilitar diagnóstico.
        try:
            send_telegram_error_log(str(exc))
        except Exception as send_exc:
            logger.error("Não foi possível enviar o log de erro para o Telegram: %s", send_exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
