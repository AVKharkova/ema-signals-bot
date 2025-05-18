import logging
import os
import sys
import asyncio
from contextlib import suppress
from datetime import datetime, timedelta

import ccxt.async_support as ccxt
import pandas as pd
from dotenv import load_dotenv
import telebot

# ----- ЛОГГИРОВАНИЕ -----
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setLevel(logging.DEBUG)
stream_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(levelname)s - %(message)s - %(name)s')
)
logger.addHandler(stream_handler)

file_handler = logging.FileHandler('ema_signals.log', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(levelname)s - %(message)s - %(name)s')
)
logger.addHandler(file_handler)

# ----- ТОКЕНЫ И ПАРАМЕТРЫ -----
load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT',
    'XRP/USDT', 'DOGE/USDT', 'AVAX/USDT', 'TON/USDT', 'ADA/USDT'
]
TIMEFRAME = '1h'
LIMIT = 150
RETRY_PERIOD = 3600  # 1 час для синхронизации с таймфреймом
ERROR_THRESHOLD = 5  # Количество ошибок перед уведомлением
STATUS_INTERVAL = 86400  # Уведомление о статусе раз в день (сек)
MIN_WAIT_SECONDS = 10  # Минимальное время ожидания для синхронизации

# ----- КАСТОМНЫЕ ИСКЛЮЧЕНИЯ -----
class MissingTokenError(Exception):
    pass

# ----- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -----
def check_tokens():
    """Проверяет наличие и валидность токенов."""
    missing = [k for k in ['TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID'] if not globals()[k]]
    if missing:
        logger.critical(f'Бот остановлен. Отсутствуют токены: {", ".join(missing)}')
        raise MissingTokenError(f'No tokens: {missing}')
    try:
        int(TELEGRAM_CHAT_ID)  # Проверка, что CHAT_ID — число
    except ValueError:
        logger.critical('TELEGRAM_CHAT_ID должен быть числом.')
        raise MissingTokenError('Invalid TELEGRAM_CHAT_ID')

def send_message(bot, message):
    """Отправляет сообщение в Telegram с логированием ошибок."""
    logger.debug(f'Отправляю сообщение: {message}')
    try:
        bot.send_message(TELEGRAM_CHAT_ID, message)
        logger.info('Сообщение отправлено в Telegram.')
    except Exception as e:
        logger.error(f'Ошибка отправки сообщения в Telegram: {e}')

async def get_ohlcv(exchange, symbol):
    """Получает OHLCV-данные с биржи."""
    try:
        data = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=LIMIT)
        if not data:
            logger.error(f'Нет данных для {symbol}')
            raise ValueError(f'No data returned for {symbol}')
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        logger.info(f'Успешно получены данные для {symbol}: {len(df)} свечей')
        if len(df) < LIMIT:
            logger.warning(f'Недостаточно данных для {symbol}: {len(df)} свечей')
        if df[['open', 'high', 'low', 'close', 'volume']].isnull().any().any():
            logger.error(f'Данные для {symbol} содержат пропуски (NaN)')
            raise ValueError(f'NaN values in {symbol} data')
        if (df[['open', 'high', 'low', 'close']] <= 0).any().any():
            logger.error(f'Данные для {symbol} содержат неположительные цены')
            raise ValueError(f'Non-positive prices in {symbol} data')
        last_timestamp = pd.to_datetime(df['timestamp'].iloc[-1], unit='ms', utc=True)
        now = pd.Timestamp.utcnow()
        time_diff = (now - last_timestamp).total_seconds()
        if time_diff > 7200:  # 2 часа
            logger.warning(f'Данные для {symbol} ({TIMEFRAME}) устарели: последняя свеча {last_timestamp} ({time_diff/3600:.1f} часов назад)')
        return df
    except Exception as e:
        logger.error(f'Ошибка получения котировок {symbol}: {e}')
        raise

async def validate_symbols(exchange, symbols):
    """Проверяет доступность торговых пар на бирже."""
    try:
        markets = await exchange.load_markets()
        valid_symbols = [s for s in symbols if s in markets]
        if len(valid_symbols) < len(symbols):
            logger.warning(f'Недоступные пары: {set(symbols) - set(valid_symbols)}')
        if not valid_symbols:
            raise ValueError('Нет доступных торговых пар')
        return valid_symbols
    except Exception as e:
        logger.critical(f'Ошибка загрузки пар: {e}')
        raise

def calc_ema(df, period):
    """Вычисляет экспоненциальную скользящую среднюю."""
    if period <= 0:
        raise ValueError('Период EMA должен быть положительным')
    return df['close'].ewm(span=period, adjust=False).mean()

def calc_rsi(df, period=14):
    """Вычисляет RSI с защитой от деления на ноль."""
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_gain = up.rolling(window=period, min_periods=period).mean()
    avg_loss = down.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.where(avg_loss != 0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return rsi

def check_signal_ema7_30(df):
    """Проверяет сигналы для стратегии EMA7/EMA30."""
    if len(df) < LIMIT:
        logger.warning(f'Недостаточно данных для EMA7/EMA30: {len(df)} свечей')
        return None
    df['ema7'] = calc_ema(df, 7)
    df['ema30'] = calc_ema(df, 30)
    if (
        df['ema7'].iloc[-2] < df['ema30'].iloc[-2] and
        df['ema7'].iloc[-1] > df['ema30'].iloc[-1] and
        df['close'].iloc[-1] > df['ema7'].iloc[-1] and
        df['close'].iloc[-1] > df['ema30'].iloc[-1]
    ):
        return "LONG"
    elif (
        df['ema7'].iloc[-2] > df['ema30'].iloc[-2] and
        df['ema7'].iloc[-1] < df['ema30'].iloc[-1] and
        df['close'].iloc[-1] < df['ema7'].iloc[-1] and
        df['close'].iloc[-1] < df['ema30'].iloc[-1]
    ):
        return "SHORT"
    return None

def check_signal_ema9_20_rsi(df):
    """Проверяет сигналы для стратегии EMA9/EMA20 + RSI."""
    if len(df) < LIMIT:
        logger.warning(f'Недостаточно данных для EMA9/EMA20+RSI: {len(df)} свечей')
        return None
    df['ema9'] = calc_ema(df, 9)
    df['ema20'] = calc_ema(df, 20)
    df['rsi'] = calc_rsi(df, 14)
    if pd.isna(df['rsi'].iloc[-1]) or pd.isna(df['rsi'].iloc[-2]):
        logger.warning('RSI содержит NaN для последних свечей')
        return None
    if (
        df['ema9'].iloc[-2] < df['ema20'].iloc[-2] and
        df['ema9'].iloc[-1] > df['ema20'].iloc[-1] and
        df['rsi'].iloc[-2] > 55 and
        df['rsi'].iloc[-1] <= 55
    ):
        return "LONG (RSI)"
    elif (
        df['ema9'].iloc[-2] > df['ema20'].iloc[-2] and
        df['ema9'].iloc[-1] < df['ema20'].iloc[-1] and
        df['rsi'].iloc[-2] < 45 and
        df['rsi'].iloc[-1] >= 45
    ):
        return "SHORT (RSI)"
    return None

async def main():
    """Основная логика бота."""
    check_tokens()
    bot = telebot.TeleBot(TELEGRAM_TOKEN)
    exchange = ccxt.bybit({'enableRateLimit': True})
    last_signals_7_30 = {}
    last_signals_9_20_rsi = {}
    error_count = 0
    last_status_time = datetime.utcnow()

    # Проверка доступных пар
    try:
        global SYMBOLS
        SYMBOLS = await validate_symbols(exchange, SYMBOLS)
        logger.info(f'Доступные пары: {SYMBOLS}')
    except Exception as e:
        send_message(bot, 'Критическая ошибка: Не удалось загрузить торговые пары.')
        await exchange.close()
        return

    # Тестовый запрос к бирже
    try:
        test_symbol = SYMBOLS[0]
        await get_ohlcv(exchange, test_symbol)
        logger.info(f'Тестовый запрос к бирже успешен для {test_symbol}')
        send_message(bot, f'Бот запущен. Тестовый запрос к бирже успешен для {test_symbol}.')
    except Exception as e:
        logger.critical(f'Не удалось подключиться к бирже: {e}')
        send_message(bot, 'Критическая ошибка: Не удалось подключиться к бирже.')
        await exchange.close()
        return

    logger.info('EMA Signal бот (две стратегии) запущен.')

    while True:
        try:
            # Синхронизация с началом часа
            now = datetime.utcnow()
            next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            wait_seconds = max((next_hour - now).total_seconds(), MIN_WAIT_SECONDS)
            logger.debug(f'Ожидание {wait_seconds:.1f} секунд до следующей проверки в {next_hour}.')
            await asyncio.sleep(wait_seconds)

            # Асинхронное получение данных
            tasks = [get_ohlcv(exchange, symbol) for symbol in SYMBOLS]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            success_count = 0

            for symbol, result in zip(SYMBOLS, results):
                if isinstance(result, Exception):
                    logger.error(f'Ошибка обработки {symbol}: {result}')
                    continue
                success_count += 1
                df = result
                price = df['close'].iloc[-1]

                # Стратегия 1: EMA7/EMA30
                signal_7_30 = check_signal_ema7_30(df)
                if signal_7_30 and last_signals_7_30.get(symbol) != signal_7_30:
                    text = (
                        f'Стратегия EMA7/30:\n'
                        f'Сигнал {signal_7_30} по {symbol} (цена {price})\n'
                        f'Таймфрейм: {TIMEFRAME}'
                    )
                    send_message(bot, text)
                    logger.info(f'EMA7/30 — {symbol}: {signal_7_30} (цена {price})')
                    last_signals_7_30[symbol] = signal_7_30

                # Стратегия 2: EMA9/EMA20 + RSI
                signal_9_20_rsi = check_signal_ema9_20_rsi(df)
                if signal_9_20_rsi and last_signals_9_20_rsi.get(symbol) != signal_9_20_rsi:
                    text = (
                        f'Стратегия EMA9/20 + RSI:\n'
                        f'Сигнал {signal_9_20_rsi} по {symbol} (цена {price})\n'
                        f'RSI: {df["rsi"].iloc[-2]:.1f} → {df["rsi"].iloc[-1]:.1f}\n'
                        f'Таймфрейм: {TIMEFRAME}'
                    )
                    send_message(bot, text)
                    logger.info(
                        f'EMA9/20+RSI — {symbol}: {signal_9_20_rsi} (цена {price}) '
                        f'RSI: {df["rsi"].iloc[-2]:.1f}→{df["rsi"].iloc[-1]:.1f}'
                    )
                    last_signals_9_20_rsi[symbol] = signal_9_20_rsi

            logger.info(f'Успешно обработано {success_count}/{len(SYMBOLS)} пар')

            # Сброс счетчика ошибок при частичном успехе
            if success_count > 0:
                error_count = 0

            # Периодическое уведомление о статусе
            now = datetime.utcnow()
            if (now - last_status_time).total_seconds() >= STATUS_INTERVAL:
                send_message(bot, f'Бот работает. Проверено {success_count}/{len(SYMBOLS)} пар.')
                last_status_time = now

        except Exception as error:
            error_count += 1
            logger.error(f'Критическая ошибка: {error}')
            if error_count >= ERROR_THRESHOLD:
                send_message(bot, 'Критическая ошибка: Бот приостановлен из-за повторяющихся сбоев.')
                logger.critical('Бот остановлен из-за превышения порога ошибок.')
                break

    await exchange.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('Бот остановлен вручную.')
    except Exception as e:
        logger.critical(f'Критическая ошибка: {e}')
