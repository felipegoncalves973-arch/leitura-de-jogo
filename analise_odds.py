#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analise_odds.py
Obtém eventos e odds do The Odds API para competições específicas.
As chaves de esporte seguem exatamente os códigos esperados pela API.
"""

import os
import time
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Configurações de logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dicionário de competições com chaves de esporte exatas da API
# ---------------------------------------------------------------------------
COMPETITIONS: List[Dict[str, str]] = [
    {
        "name": "Copa do Mundo",
        "sport": "soccer_fifa_world_cup",
        "region": "eu",
    },
    {
        "name": "Série B do Brasil",
        "sport": "soccer_brazil_serie_b",
        "region": "br",
    },
]

# ---------------------------------------------------------------------------
# Variáveis de ambiente / configuração
# ---------------------------------------------------------------------------
ODDS_API_KEY: Optional[str] = os.environ.get("ODDS_API_KEY")
ODDS_API_HOST: str = "https://api.the-odds-api.com"
REQUEST_TIMEOUT: int = int(os.environ.get("ODDS_TIMEOUT", "30"))
MAX_RETRIES: int = int(os.environ.get("ODDS_MAX_RETRIES", "3"))
RETRY_DELAY_SECONDS: float = float(os.environ.get("ODDS_RETRY_DELAY", "2"))

# Mercados que serão buscados individualmente por evento
MARKETS: List[str] = [
    "h2h",
    "btts",
    "totals",
    "correct_score",
    "totals_h1",
]


# ---------------------------------------------------------------------------
# Exceções customizadas
# ---------------------------------------------------------------------------
class OddsAPIError(Exception):
    """Erro genérico da integração com a Odds API."""


class OddsAPIRateLimitError(OddsAPIError):
    """Erro específico para rate limit."""


class OddsAPIAuthError(OddsAPIError):
    """Erro específico de autenticação."""


class OddsAPIResponseError(OddsAPIError):
    """Erro em resposta inesperada da API."""


# ---------------------------------------------------------------------------
# Helpers de requisição HTTP
# ---------------------------------------------------------------------------
def _send_request(
    url: str,
    params: Dict[str, Any],
    retries: int = MAX_RETRIES,
    timeout: int = REQUEST_TIMEOUT,
) -> Dict[str, Any]:
    """
    Envia requisição GET via requests com retry simples e tratamento de erros.
    """
    attempt = 0
    last_exception: Optional[Exception] = None

    while attempt < retries:
        attempt += 1
        try:
            response = requests.get(url, params=params, timeout=timeout)
            logger.info(
                "Requisição %s (tentativa %d/%d) -> status %d",
                response.url.split("api_key")[0].rstrip("?&"),
                attempt,
                retries,
                response.status_code,
            )

            if response.status_code == 200:
                return response.json()

            if response.status_code == 401:
                raise OddsAPIAuthError("API key inválida ou não autorizada.")
            if response.status_code == 429:
                raise OddsAPIRateLimitError("Rate limit atingido.")
            if response.status_code >= 500:
                raise OddsAPIResponseError(
                    f"Erro do servidor ({response.status_code})."
                )
            raise OddsAPIResponseError(
                f"Resposta inesperada: {response.status_code} - {response.text}"
            )

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_exception = exc
            logger.warning(
                "Erro de conexão/timeout na tentativa %d/%d: %s",
                attempt,
                retries,
                exc,
            )
            if attempt < retries:
                time.sleep(RETRY_DELAY_SECONDS * attempt)
            continue
        except requests.exceptions.RequestException as exc:
            logger.exception("Falha inesperada na requisição")
            raise OddsAPIError(f"Falha na requisição: {exc}") from exc

    raise OddsAPIError(
        f"Número máximo de tentativas excedido. Último erro: {last_exception}"
    )


# ---------------------------------------------------------------------------
# Integração com a API
# ---------------------------------------------------------------------------
def get_sports() -> List[Dict[str, str]]:
    """
    Lista esportes disponíveis na API. Útil para diagnóstico.
    """
    if not ODDS_API_KEY:
        raise OddsAPIAuthError("Variável de ambiente ODDS_API_KEY não configurada.")

    url = f"{ODDS_API_HOST}/v4/sports"
    params = {"apiKey": ODDS_API_KEY}

    data = _send_request(url, params)
    if not isinstance(data, list):
        raise OddsAPIResponseError("Formato inesperado ao listar esportes.")

    return [
        {"key": sport.get("key", ""), "title": sport.get("title", "")}
        for sport in data
    ]


def get_events_for_competition(
    sport_key: str,
    region: str,
    odds_format: str = "decimal",
    date_format: str = "iso",
) -> List[Dict[str, Any]]:
    """
    Busca os eventos futuros de uma competição/esporte específico.
    """
    if not ODDS_API_KEY:
        raise OddsAPIAuthError("Variável de ambiente ODDS_API_KEY não configurada.")

    url = f"{ODDS_API_HOST}/v4/sports/{sport_key}/events"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": region,
        "oddsFormat": odds_format,
        "dateFormat": date_format,
    }

    data = _send_request(url, params)
    if not isinstance(data, list):
        raise OddsAPIResponseError(
            f"Esperado uma lista de eventos, obtido: {type(data).__name__}"
        )
    return data


def get_event_odds(
    sport_key: str,
    event_id: str,
    region: str,
    market: str,
    odds_format: str = "decimal",
    date_format: str = "iso",
) -> Optional[Dict[str, Any]]:
    """
    Busca odds de um mercado específico para um evento específico.
    """
    if not ODDS_API_KEY:
        raise OddsAPIAuthError("Variável de ambiente ODDS_API_KEY não configurada.")

    url = f"{ODDS_API_HOST}/v4/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": region,
        "markets": market,
        "oddsFormat": odds_format,
        "dateFormat": date_format,
    }

    try:
        data = _send_request(url, params)
    except OddsAPIResponseError as exc:
        logger.warning("Não foi possível obter odds para %s (%s): %s", event_id, market, exc)
        return None

    return data


# ---------------------------------------------------------------------------
# Processamento de dados
# ---------------------------------------------------------------------------
def parse_event_basic(event: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai informações básicas de um evento."""
    return {
        "event_id": event.get("id"),
        "sport_key": event.get("sport_key"),
        "sport_title": event.get("sport_title"),
        "home_team": event.get("home_team"),
        "away_team": event.get("away_team"),
        "commence_time": event.get("commence_time"),
    }


def enrich_event_with_markets(
    sport_key: str,
    region: str,
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Para cada evento, busca individualmente os mercados configurados e
    os anexa ao dicionário do evento.
    """
    event_id = event.get("id")
    if not event_id:
        logger.warning("Evento sem ID, ignorando: %s", event)
        return event

    enriched = parse_event_basic(event)
    enriched["markets"] = {}

    for market in MARKETS:
        try:
            market_data = get_event_odds(
                sport_key=sport_key,
                event_id=str(event_id),
                region=region,
                market=market,
            )
            if market_data is None:
                enriched["markets"][market] = None
                continue

            bookmakers = market_data.get("bookmakers", [])
            enriched["markets"][market] = {
                "bookmakers_count": len(bookmakers),
                "bookmakers": bookmakers,
            }
            logger.info(
                "Market '%s' obtido para evento %s (%s bookmakers)",
                market,
                event_id,
                len(bookmakers),
            )
        except Exception as exc:
            logger.warning("Falha ao obter market '%s' para evento %s: %s", market, event_id, exc)
            enriched["markets"][market] = {"error": str(exc)}

        # Pequeno throttle para evitar estourar rate limit
        time.sleep(0.25)

    return enriched


def analyze_competition(competition: Dict[str, str]) -> Dict[str, Any]:
    """
    Executa a análise completa para uma competição: lista eventos e busca
    odds de cada mercado para cada evento.
    """
    name = competition["name"]
    sport_key = competition["sport"]
    region = competition["region"]

    logger.info("Iniciando análise da competição: %s [%s]", name, sport_key)

    try:
        events = get_events_for_competition(sport_key, region)
    except OddsAPIError as exc:
        logger.error("Erro ao buscar eventos para %s: %s", name, exc)
        return {
            "competition": name,
            "sport_key": sport_key,
            "region": region,
            "error": str(exc),
            "events": [],
        }

    enriched_events = []
    for event in events:
        try:
            enriched = enrich_event_with_markets(sport_key, region, event)
            enriched_events.append(enriched)
        except Exception as exc:
            logger.error("Erro inesperado ao processar evento %s: %s", event.get("id"), exc)
            continue

    return {
        "competition": name,
        "sport_key": sport_key,
        "region": region,
        "events_count": len(enriched_events),
        "events": enriched_events,
    }


# ---------------------------------------------------------------------------
# Saída / exportação
# ---------------------------------------------------------------------------
def save_results(data: Dict[str, Any], filepath: str = "analise_odds_output.json") -> None:
    """Salva o resultado consolidado em arquivo JSON."""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("Resultado salvo em: %s", filepath)
    except OSError as exc:
        logger.error("Não foi possível salvar o arquivo %s: %s", filepath, exc)


def main() -> None:
    """Fluxo principal."""
    if not ODDS_API_KEY:
        logger.error(
            "ODDS_API_KEY não encontrada. Configure a variável de ambiente antes de executar."
        )
        return

    timestamp = datetime.now(timezone.utc).isoformat()
    results = {
        "generated_at": timestamp,
        "competitions": [],
    }

    for competition in COMPETITIONS:
        try:
            competition_result = analyze_competition(competition)
            results["competitions"].append(competition_result)
        except Exception as exc:
            logger.exception("Erro fatal ao processar competição %s", competition.get("name"))
            results["competitions"].append(
                {
                    "competition": competition.get("name"),
                    "sport_key": competition.get("sport"),
                    "region": competition.get("region"),
                    "error": str(exc),
                    "events": [],
                }
            )

    save_results(results)
    logger.info("Análise finalizada. Total de competições processadas: %d", len(results["competitions"]))


if __name__ == "__main__":
    main()
