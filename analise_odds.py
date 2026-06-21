import os
import time
import requests
from datetime import datetime, timezone, timedelta

# ==================== CONFIGURAÇÕES ====================
API_KEY = os.getenv("ODDS_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

LIGAS_PREFERIDAS = [
    ("Copa do Mundo", "soccer_fifa_world_cup"),
    ("Série B do Brasil", "soccer_brazil_serie_b"),
    ("Série A do Brasil", "soccer_brazil_campeonato"),
]

SPORT = "soccer"
REGIONS = "eu"
MARKETS = "h2h"
ODDS_FORMAT = "decimal"
HORAS_FUTURO = 72

OVERROUND_ALERTA = 1.10
OVERROUND_LIMITE = 1.15

INTERVALO_SEGUNDOS = 60 * 60

# ==================== FUNÇÕES AUXILIARES ====================

def enviar_telegram(mensagem):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[AVISO] Telegram não configurado. Mensagem não enviada.")
        print(mensagem)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensagem,
        "parse_mode": "Markdown",
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[ERRO] Falha ao enviar mensagem Telegram: {e}")
        return False


def buscar_jogos_liga(tournament_key):
    if not API_KEY:
        print("[ERRO] ODDS_API_KEY não configurada.")
        return []

    url = f"https://api.the-odds-api.com/v4/sports/{tournament_key}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": REGIONS,
        "markets": MARKETS,
        "oddsFormat": ODDS_FORMAT,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        if response.status_code == 404:
            print(f"[INFO] Liga {tournament_key} não disponível ou inativa na API (404).")
            return []
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"[ERRO] Falha ao buscar odds para {tournament_key}: {e}")
        return []


def calcular_probabilidades_implícitas(odds):
    return [1 / o if o > 0 else 0 for o in odds]


def calcular_overround(probs):
    return sum(probs)


def formatar_probabilidades(probs, times):
    linhas = []
    for time_nome, prob in zip(times, probs):
        linhas.append(f"  - {time_nome}: {prob:.2%}")
    return "\n".join(linhas)


def processar_jogo(jogo, nome_liga, tournament_key):
    home_team = jogo.get("home_team", "Desconhecido")
    away_team = jogo.get("away_team", "Desconhecido")
    commence_time = jogo.get("commence_time")
    bookmakers = jogo.get("bookmakers", [])

    if not bookmakers:
        return None

    melhor_bookmaker = bookmakers[0]
    outcomes = melhor_bookmaker.get("markets", [{}])[0].get("outcomes", [])

    if len(outcomes) < 2:
        return None

    odds = [outcomes[0].get("price", 0), outcomes[1].get("price", 0)]
    if len(outcomes) >= 3:
        odds.append(outcomes[2].get("price", 0))

    if any(o <= 0 for o in odds):
        return None

    times = [home_team, away_team]
    if len(odds) == 3:
        times.append("Empate")

    probs = calcular_probabilidades_implícitas(odds)
    overround = calcular_overround(probs)

    status = "Normal"
    if overround >= OVERROUND_LIMITE:
        status = f"⚠️ Overround elevado ({overround:.2%})"
    elif overround >= OVERROUND_ALERTA:
        status = f"⚡ Overround acima do ideal ({overround:.2%})"

    inicio = "Não informado"
    if commence_time:
        try:
            dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
            inicio = dt.strftime("%d/%m/%Y %H:%M UTC")
        except ValueError:
            inicio = commence_time

    mensagem = (
        f"🏆 *{nome_liga}*\n"
        f"⚽ *{home_team}* x *{away_team}*\n"
        f"🕒 Início: {inicio}\n"
        f"🏢 Bookmaker: {melhor_bookmaker.get('title', 'N/A')}\n\n"
        f"📊 *Probabilidades implícitas:*\n"
        f"{formatar_probabilidades(probs, times)}\n\n"
        f"🧮 *Overround:* {overround:.4f} ({overround:.2%})\n"
        f"{status}\n"
        f"─────────────────────"
    )

    return {
        "mensagem": mensagem,
        "overround": overround,
        "inicio": inicio,
    }


def main():
    agora = datetime.now(timezone.utc)
    limite_futuro = agora + timedelta(hours=HORAS_FUTURO)

    total_alertas = 0
    ligas_ativas = 0

    for nome_liga, tournament_key in LIGAS_PREFERIDAS:
        print(f"[INFO] Verificando {nome_liga} ({tournament_key})...")
        jogos = buscar_jogos_liga(tournament_key)

        if not jogos:
            continue

        ligas_ativas += 1
        jogos_liga = 0

        for jogo in jogos:
            commence_time = jogo.get("commence_time")
            if not commence_time:
                continue

            try:
                dt_inicio = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
            except ValueError:
                continue

            if not (agora <= dt_inicio <= limite_futuro):
                continue

            resultado = processar_jogo(jogo, nome_liga, tournament_key)
            if resultado:
                enviar_telegram(resultado["mensagem"])
                total_alertas += 1
                jogos_liga += 1
                time.sleep(1)

        print(f"[INFO] {nome_liga}: {jogos_liga} jogo(s) enviado(s).")

    if total_alertas == 0:
        mensagem = (
            "🤖 *Robô de Análise de Odds*\n"
            "O robô está ativo, mas *não foram encontrados jogos agendados* "
            f"nas próximas *{HORAS_FUTURO} horas* para as ligas monitoradas:\n\n"
            "• Copa do Mundo\n"
            "• Série B do Brasil\n"
            "• Série A do Brasil\n\n"
            "Aguardando novos eventos..."
        )
        enviar_telegram(mensagem)
        print("[INFO] Nenhum jogo encontrado. Mensagem de status enviada.")
    else:
        print(f"[INFO] Total de alertas enviados: {total_alertas}")


if __name__ == "__main__":
    main()
