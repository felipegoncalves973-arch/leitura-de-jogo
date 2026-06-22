import os
import time
import logging
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

# Configurações
API_KEY = os.getenv('API_KEY_ODDS', 'SUA_API_KEY_AQUI')
REGIAO = 'eu'
ODDS_FORMAT = 'decimal'
MARKETS = 'h2h'
HORAS_FUTURO = 24
BOOKMAKER_EXCHANGE = 'betfair_ex'
BOOKMAKER_FALLBACK = 'betfair'

# Esportes a monitorar
SPORTS = {
    'soccer_fifa_world_cup': 'Copa do Mundo',
    'soccer_brazil_serie_b': 'Série B do Brasil'
}

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'SEU_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', 'SEU_CHAT_ID')

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('analise_odds.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def enviar_telegram(mensagem):
    try:
        url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': mensagem,
            'parse_mode': 'Markdown'
        }
        resp = requests.post(url, data=payload, timeout=15)
        resp.raise_for_status()
        logger.info('Mensagem enviada ao Telegram com sucesso.')
    except Exception as e:
        logger.error(f'Erro ao enviar mensagem para o Telegram: {e}')


def buscar_eventos(sport):
    try:
        agora = datetime.now(timezone.utc)
        inicio = agora.strftime('%Y-%m-%dT%H:%M:%SZ')
        fim = (agora + timedelta(hours=HORAS_FUTURO)).strftime('%Y-%m-%dT%H:%M:%SZ')

        params = {
            'apiKey': API_KEY,
            'regions': REGIAO,
            'oddsFormat': ODDS_FORMAT,
            'markets': MARKETS,
            'commenceTimeFrom': inicio,
            'commenceTimeTo': fim,
        }

        url = f'https://api.the-odds-api.com/v4/sports/{sport}/odds/?{urlencode(params)}'
        logger.info(f'Buscando eventos para {sport}: {url.replace(API_KEY, "***")}')

        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        dados = resp.json()

        if not isinstance(dados, list):
            logger.error(f'Resposta inesperada da API para {sport}: {dados}')
            return []

        logger.info(f'{len(dados)} eventos encontrados para {sport}.')
        return dados
    except requests.exceptions.RequestException as e:
        logger.error(f'Erro de requisição para {sport}: {e}')
    except Exception as e:
        logger.error(f'Erro ao processar dados de {sport}: {e}')
    return []


def selecionar_bookmaker(evento):
    bookmakers = evento.get('bookmakers', [])
    if not isinstance(bookmakers, list):
        return None

    for bm in bookmakers:
        if bm.get('key') == BOOKMAKER_EXCHANGE:
            return bm, 'Betfair Exchange'

    for bm in bookmakers:
        if bm.get('key') == BOOKMAKER_FALLBACK:
            return bm, 'Betfair'

    return None


def extrair_odds(bookmaker_data):
    try:
        mercados = bookmaker_data.get('markets', [])
        for mercado in mercados:
            if mercado.get('key') == MARKETS:
                resultados = mercado.get('outcomes', [])
                if len(resultados) >= 2:
                    casa = resultados[0]
                    empate = resultados[1] if len(resultados) == 3 else None
                    fora = resultados[2] if len(resultados) == 3 else resultados[1]
                    return {
                        'casa': casa.get('name', 'Casa'),
                        'odd_casa': casa.get('price'),
                        'empate': empate.get('name', 'Empate') if empate else None,
                        'odd_empate': empate.get('price') if empate else None,
                        'fora': fora.get('name', 'Fora'),
                        'odd_fora': fora.get('price')
                    }
        return None
    except Exception as e:
        logger.error(f'Erro ao extrair odds: {e}')
        return None


def formatar_data(commence_time):
    try:
        dt = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
        dt = dt.astimezone(timezone(timedelta(hours=-3)))  # Brasília
        return dt.strftime('%d/%m/%Y %H:%M')
    except Exception as e:
        logger.error(f'Erro ao formatar data {commence_time}: {e}')
        return commence_time


def analisar_odds(odds):
    casa = odds['odd_casa']
    empate = odds['odd_empate']
    fora = odds['odd_fora']

    # Evita divisão por zero
    if not casa or not fora or casa <= 0 or fora <= 0:
        return 'dados_incompletos'

    if empate and empate > 0:
        overround = (1 / casa) + (1 / empate) + (1 / fora)
    else:
        overround = (1 / casa) + (1 / fora)

    # Destaque para odds equilibradas com overround baixo
    if 1.8 <= casa <= 3.5 and 1.8 <= fora <= 3.5:
        if overround < 1.10:
            return 'destaque'

    return 'normal'


def processar_evento(evento, nome_campeonato):
    try:
        id_evento = evento.get('id', 'N/A')
        home = evento.get('home_team', 'N/A')
        away = evento.get('away_team', 'N/A')
        commence = evento.get('commence_time', 'N/A')
        data_hora = formatar_data(commence)

        selecao = selecionar_bookmaker(evento)
        if selecao is None:
            logger.warning(f'Bookmaker Betfair não disponível para {home} x {away}. Evento ignorado.')
            return None

        bookmaker, nome_casa = selecao
        odds = extrair_odds(bookmaker)

        if not odds or odds['odd_casa'] is None or odds['odd_fora'] is None:
            logger.warning(f'Odds incompletas para {home} x {away} na {nome_casa}. Evento ignorado.')
            return None

        situacao = analisar_odds(odds)

        linha = f'{home} x {away}\n'
        linha += f'Campeonato: {nome_campeonato}\n'
        linha += f'Data/Horário: {data_hora} (Brasília)\n'
        linha += f'Casa: *Betfair Exchange*\n' if nome_casa == 'Betfair Exchange' else f'Casa: *Betfair* (fallback)\n'
        linha += f'Odds (1X2): {odds["odd_casa"]}'
        if odds['odd_empate']:
            linha += f' | {odds["odd_empate"]}'
        linha += f' | {odds["odd_fora"]}\n'

        if situacao == 'destaque':
            linha += '🔥 *Oportunidade destacada: odds equilibradas e overround baixo.*'
        else:
            linha += '✅ Evento dentro dos parâmetros monitorados.'

        logger.info(f'Evento processado: {home} x {away} ({nome_casa})')
        return linha
    except Exception as e:
        logger.error(f'Erro ao processar evento {evento.get("id", "N/A")}: {e}')
        return None


def main():
    logger.info('Iniciando análise de odds.')
    mensagens = []

    for sport, nome_campeonato in SPORTS.items():
        eventos = buscar_eventos(sport)
        for evento in eventos:
            msg = processar_evento(evento, nome_campeonato)
            if msg:
                mensagens.append(msg)
        time.sleep(1)  # Respeitar limites de requisição

    if mensagens:
        cabecalho = f'*Análise de Odds - Próximas {HORAS_FUTURO}h*\n\n'
        corpo = '\n\n'.join(mensagens)
        enviar_telegram(cabecalho + corpo)
    else:
        logger.info('Nenhum evento elegível encontrado para envio.')
        enviar_telegram(f'Nenhuma oportunidade encontrada nas próximas {HORAS_FUTURO}h para as competições monitoradas.')

    logger.info('Análise de odds finalizada.')


if __name__ == '__main__':
    main()
