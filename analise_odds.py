import os
import sys
import time
import json
import logging
import traceback
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

import requests
import pandas as pd

# ============================================================
# CONFIGURAÇÕES E CONSTANTES
# ============================================================

# The Odds API
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "SUA_API_KEY_AQUI")
ODDS_API_HOST = "https://api.the-odds-api.com"
ODDS_ENDPOINT = f"{ODDS_API_HOST}/v4/sports/soccer/odds"

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "SEU_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "SEU_CHAT_ID")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("analise_odds.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# Mercados suportados (correct_score removido para evitar erro 422 na API)
SUPPORTED_MARKETS = [
    "h2h",
    "spreads",
    "totals",
    "outrights",
    "h2h_lay",
    "outrights_lay",
    "over_under",
    "asian_handicap",
    "draw_no_bet",
    "both_teams_to_score",
    "half_time_full_time",
    "first_half_h2h",
    "first_half_spreads",
    "first_half_totals",
    "second_half_h2h",
    "second_half_spreads",
    "second_half_totals",
]

# Mercado padrão de busca (sem correct_score)
DEFAULT_MARKETS = ["h2h", "totals", "both_teams_to_score", "draw_no_bet"]

# Regiões e ligas padrão
REGIONS = ["eu", "uk", "us"]
SLEEP_BETWEEN_REQUESTS = 1.2


# ============================================================
# ESTRUTURAS DE DADOS
# ============================================================

@dataclass
class Evento:
    id_evento: str
    home_team: str
    away_team: str
    commence_time: datetime
    liga: str

    def nome_partida(self) -> str:
        return f"{self.home_team} x {self.away_team}"


@dataclass
class Oportunidade:
    evento: Evento
    mercado: str
    casa: str
    tipo_entrada: str
    linha: Optional[float]
    odd: float
    stake: Optional[float] = None
    confianca: Optional[float] = None
    justificativa: str = ""


# ============================================================
# UTILITÁRIOS
# ============================================================

def safe_get(dado: Any, chave: str, padrao: Any = None) -> Any:
    """Retorna dado.get(chave) de forma segura, mesmo se dado for None."""
    if isinstance(dado, dict):
        return dado.get(chave, padrao)
    return padrao


def formatar_odd(odd: float) -> str:
    return f"{odd:.2f}"


def formatar_mensagem_telegram(oportunidades: List[Oportunidade]) -> str:
    """Formata oportunidades em mensagem HTML segura para o Telegram."""
    if not oportunidades:
        return "<<b>⚽ Análise de Odds</b>\nNenhuma oportunidade identificada nesta rodada."

    linhas = ["<<b>⚽ Oportunidades de Valor Encontradas</b>\n"]
    for op in oportunidades:
        data_str = op.evento.commence_time.strftime("%d/%m %H:%M")
        linha = (
            f"<<b>Liga:</b> {op.evento.liga}\n"
            f"<<b>Jogo:</b> {op.evento.nome_partida()}\n"
            f"<<b>Data:</b> {data_str}\n"
            f"<<b>Mercado:</b> {op.mercado}\n"
            f"<<b>Casa:</b> {op.casa}\n"
            f"<<b>Tipo:</b> {op.tipo_entrada}\n"
            f"<<b>Linha:</b> {op.linha if op.linha is not None else '-'}\n"
            f"<<b>Odd:</b> {formatar_odd(op.odd)}\n"
            f"<<b>Confiança:</b> {op.confianca if op.confianca is not None else '-'}\n"
            f"<<b>Justificativa:</b> {op.justificativa}\n"
            f"{'─' * 30}\n"
        )
        linhas.append(linha)
    return "\n".join(linhas)


# ============================================================
# TELEGRAM
# ============================================================

def enviar_telegram(mensagem: str) -> bool:
    """Envia mensagem formatada via Telegram usando requests.post e HTML."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Token ou chat_id do Telegram não configurados. Mensagem não enviada.")
        return False

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensagem,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(TELEGRAM_API_URL, json=payload, timeout=30)
        response.raise_for_status()
        logger.info("Mensagem enviada para o Telegram com sucesso.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error("Falha ao enviar mensagem para o Telegram: %s", e)
        return False
    except Exception as e:
        logger.error("Erro inesperado ao enviar mensagem para o Telegram: %s", e)
        return False


# ============================================================
# API THE ODDS
# ============================================================

def buscar_eventos(
    regions: List[str] = REGIONS,
    markets: List[str] = DEFAULT_MARKETS,
    dias_a_frente: int = 2,
) -> List[Dict[str, Any]]:
    """Busca eventos de futebol na API, filtrando mercados inválidos."""
    # Garante que correct_score nunca seja solicitado
    mercados_validos = [m for m in markets if m != "correct_score"]
    if not mercados_validos:
        mercados_validos = DEFAULT_MARKETS

    markets_str = ",".join(mercados_validos)
    inicio = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    fim = (datetime.utcnow() + timedelta(days=dias_a_frente)).strftime("%Y-%m-%dT%H:%M:%SZ")

    eventos: List[Dict[str, Any]] = []
    for region in regions:
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": region,
            "markets": markets_str,
            "oddsFormat": "decimal",
            "dateFormat": "iso",
            "commenceTimeFrom": inicio,
            "commenceTimeTo": fim,
        }
        try:
            logger.info("Buscando eventos: region=%s, markets=%s", region, markets_str)
            response = requests.get(ODDS_ENDPOINT, params=params, timeout=60)

            if response.status_code == 422:
                logger.warning(
                    "Erro 422 (INVALID_MARKET) para region=%s, markets=%s. Ignorando e continuando.",
                    region,
                    markets_str,
                )
                continue

            response.raise_for_status()
            dados = response.json()
            if isinstance(dados, list):
                eventos.extend(dados)
            else:
                logger.warning("Resposta inesperada da API: %s", dados)

        except requests.exceptions.RequestException as e:
            logger.error("Erro na requisição para region=%s: %s", region, e)
        except Exception as e:
            logger.error("Erro inesperado ao buscar eventos region=%s: %s", region, e)

        time.sleep(SLEEP_BETWEEN_REQUESTS)

    logger.info("Total de eventos obtidos: %d", len(eventos))
    return eventos


def parse_evento(evento_raw: Dict[str, Any]) -> Optional[Evento]:
    """Converte um evento bruto da API em um objeto Evento."""
    try:
        event_id = evento_raw.get("id")
        home = safe_get(evento_raw, "home_team")
        away = safe_get(evento_raw, "away_team")
        commence_str = safe_get(evento_raw, "commence_time")
        liga = safe_get(evento_raw, "sport_title", "Futebol")

        if not all([event_id, home, away, commence_str]):
            return None

        commence_time = datetime.fromisoformat(commence_str.replace("Z", "+00:00"))
        return Evento(
            id_evento=event_id,
            home_team=home,
            away_team=away,
            commence_time=commence_time,
            liga=liga,
        )
    except Exception as e:
        logger.error("Erro ao fazer parse de evento: %s", e)
        return None


# ============================================================
# ANÁLISE DE ODD
# ============================================================

def analisar_mercado(
    evento: Evento,
    mercado: str,
    market_data: Optional[Dict[str, Any]],
) -> List[Oportunidade]:
    """Analisa um mercado e retorna oportunidades identificadas."""
    oportunidades: List[Oportunidade] = []

    # Correção segura do bug 'NoneType' object has no attribute 'get'
    if not isinstance(market_data, dict):
        logger.debug("market_data ausente ou inválido para evento %s", evento.id_evento)
        return oportunidades

    # correct_score nunca é solicitado, mas trata de forma segura caso apareça
    if mercado == "correct_score":
        logger.debug("Ignorando mercado correct_score para evento %s", evento.id_evento)
        return oportunidades

    outcomes = safe_get(market_data, "outcomes", [])
    if not isinstance(outcomes, list) or not outcomes:
        return oportunidades

    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        odd = safe_get(outcome, "price")
        if not isinstance(odd, (int, float)) or odd <= 1.0:
            continue

        # Exemplo simples de identificação de valor: odd >= 2.0
        if odd >= 2.0:
            oportunidades.append(
                Oportunidade(
                    evento=evento,
                    mercado=mercado,
                    casa=safe_get(market_data, "key", "desconhecida"),
                    tipo_entrada=safe_get(outcome, "name", "n/a"),
                    linha=safe_get(outcome, "point"),
                    odd=float(odd),
                    confianca=0.65,
                    justificativa="Odd detectada acima do limiar de valor configurado.",
                )
            )

    return oportunidades


def processar_eventos(eventos_raw: List[Dict[str, Any]]) -> List[Oportunidade]:
    """Processa a lista de eventos e extrai oportunidades de valor."""
    oportunidades: List[Oportunidade] = []

    for evento_raw in eventos_raw:
        evento = parse_evento(evento_raw)
        if not evento:
            continue

        bookmakers = safe_get(evento_raw, "bookmakers", [])
        if not isinstance(bookmakers, list):
            continue

        for bookmaker in bookmakers:
            if not isinstance(bookmaker, dict):
                continue
            mercados = safe_get(bookmaker, "markets", [])
            if not isinstance(mercados, list):
                continue

            for market in mercados:
                if not isinstance(market, dict):
                    continue
                market_key = safe_get(market, "key")
                if market_key == "correct_score":
                    continue

                oportunidades.extend(
                    analisar_mercado(evento=evento, mercado=str(market_key), market_data=market)
                )

    return oportunidades


# ============================================================
# RELATÓRIO E EXECUÇÃO
# ============================================================

def gerar_resumo(oportunidades: List[Oportunidade]) -> pd.DataFrame:
    """Gera um DataFrame resumo das oportunidades."""
    dados = []
    for op in oportunidades:
        dados.append(
            {
                "Liga": op.evento.liga,
                "Partida": op.evento.nome_partida(),
                "Data": op.evento.commence_time.strftime("%d/%m/%Y %H:%M"),
                "Mercado": op.mercado,
                "Casa": op.casa,
                "Tipo": op.tipo_entrada,
                "Linha": op.linha,
                "Odd": op.odd,
                "Confiança": op.confianca,
                "Justificativa": op.justificativa,
            }
        )
    return pd.DataFrame(dados)


def main() -> None:
    logger.info("Iniciando analise_odds.py")

    try:
        if ODDS_API_KEY in ("SUA_API_KEY_AQUI", None, ""):
            logger.error("ODDS_API_KEY não configurada. Configure a variável de ambiente.")
            sys.exit(1)

        eventos_raw = buscar_eventos()
        if not eventos_raw:
            logger.info("Nenhum evento retornado pela API.")
            mensagem = formatar_mensagem_telegram([])
            enviar_telegram(mensagem)
            return

        oportunidades = processar_eventos(eventos_raw)
        df = gerar_resumo(oportunidades)

        if not df.empty:
            df.to_csv("oportunidades.csv", index=False, encoding="utf-8-sig")
            logger.info("Oportunidades salvas em oportunidades.csv (%d registros)", len(df))
        else:
            logger.info("Nenhuma oportunidade identificada.")

        mensagem = formatar_mensagem_telegram(oportunidades)
        enviado = enviar_telegram(mensagem)
        if not enviado:
            logger.warning("Mensagem do Telegram não foi enviada, mas o script continua.")

        logger.info("analise_odds.py finalizado com sucesso.")

    except Exception as e:
        logger.error("Erro fatal na execução: %s", e)
        logger.error(traceback.format_exc())
        mensagem_erro = f"<<b>⚠️ Erro fatal em analise_odds.py</b>\n<pre>{str(e)}</pre>"
        enviar_telegram(mensagem_erro)
        sys.exit(1)


if __name__ == "__main__":
    main()
