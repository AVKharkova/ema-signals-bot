import asyncio
from datetime import datetime, timedelta, timezone
import ccxt.async_support as ccxt
import pandas as pd
import telebot
from telebot.storage import StateMemoryStorage

from src.config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TP_PERCENT, SL_PERCENT,
    SYMBOLS, TIMEFRAME, LIMIT, ERROR_THRESHOLD, STATUS_INTERVAL, PING_INTERVAL, MIN_WAIT_SECONDS
)
from src.utils import setup_logging, check_tokens, send_message, send_critical_message, MissingTokenError
from src.strategies import check_signal_ema7_30, check_signal_ema9_20_rsi
from src.handlers import register_handlers, setup_bot_commands

# Инициализация логгера
logger = setup_logging()

async def get_ohlcv(exchange, symbol, log_candles=False):
    # Получение OHLCV-данных с биржи с проверкой их валидности
    try:
        data = await asyncio.wait_for(
            exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=LIMIT),
            timeout=30.0
        )
        if not data:
            logger.error(f'Нет данных для {symbol}')
            raise ValueError(f'Нет данных для {symbol}')
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        # Логируем количество свечей для каждой пары, если log_candles=True
        if log_candles:
            logger.info(f'Успешно получено {len(df)} свечей для {symbol}')
        if len(df) < LIMIT:
            logger.warning(f'Недостаточно данных для {symbol}: {len(df)} свечей')
        if df[['open', 'high', 'low', 'close', 'volume']].isnull().any().any():
            logger.error(f'Данные для {symbol} содержат пропуски (NaN)')
            raise ValueError(f'Пропуски в данных для {symbol}')
        if (df[['open', 'high', 'low', 'close']] <= 0).any().any():
            logger.error(f'Данные для {symbol} содержат неположительные цены')
            raise ValueError(f'Неположительные цены в данных для {symbol}')
        last_timestamp = pd.to_datetime(df['timestamp'].iloc[-1], unit='ms', utc=True)
        now = pd.Timestamp.now(timezone.utc)
        time_diff = (now - last_timestamp).total_seconds()
        if time_diff > 7200:
            logger.warning(
                f'Данные для {symbol} ({TIMEFRAME}) устарели: последняя свеча {last_timestamp} '
                f'({time_diff/3600:.1f} часов назад)'
            )
        return df
    except asyncio.TimeoutError:
        logger.error(f'Тайм-аут при получении OHLCV для {symbol}: превышено время ожидания (30 секунд)')
        raise
    except Exception as e:
        logger.error(f'Ошибка получения котировок для {symbol}: {e}')
        raise

async def validate_symbols(exchange, symbols):
    # Проверка доступности торговых пар на бирже
    logger.debug("Проверка доступности торговых пар")
    try:
        markets = await exchange.load_markets()
        valid_symbols = [s for s in symbols if s in markets]
        if len(valid_symbols) < len(symbols):
            logger.warning(f'Недоступные пары: {set(symbols) - set(valid_symbols)}')
        if not valid_symbols:
            raise ValueError('Нет доступных торговых пар')
        logger.debug(f"Доступные пары: {valid_symbols}")
        return valid_symbols
    except Exception as e:
        logger.critical(f'Ошибка загрузки торговых пар: {e}')
        raise

def calc_oco_prices(direction, price, tp_perc=TP_PERCENT, sl_perc=SL_PERCENT):
    # Расчёт цен для OCO-ордера (тейк-профит и стоп-лосс)
    if direction.startswith('LONG'):
        tp_price = round(price * (1 + tp_perc/100), 4)
        sl_trigger = round(price * (1 - sl_perc/100), 4)
        sl_market = round(sl_trigger * 0.999, 4)
    else:  # SHORT
        tp_price = round(price * (1 - tp_perc/100), 4)
        sl_trigger = round(price * (1 + sl_perc/100), 4)
        sl_market = round(sl_trigger * 1.001, 4)
    return tp_price, sl_trigger, sl_market

def format_signal_message(symbol, signal_type, price, tp_price, sl_trigger, sl_market, timeframe, rsi_data=None):
    # Форматирование сообщения о торговом сигнале для Telegram
    message = f"**📊 {signal_type} Сигнал для {symbol}**\n"
    message += f"*Цена*: `{price:.4f}`\n"
    message += f"*Таймфрейм*: `{timeframe}`\n"
    if rsi_data:
        prev_rsi, curr_rsi = rsi_data
        message += f"*RSI*: `{prev_rsi:.1f} → {curr_rsi:.1f}`\n"
    message += "\n**📈 Настройки OCO-ордера**\n"
    message += f"- *Тейк-профит (Лимит)*: `{tp_price:.4f}`\n"
    message += f"- *Стоп-лосс (Триггер)*: `{sl_trigger:.4f}`\n"
    message += f"- *Стоп-лосс (Рыночная)*: `{sl_market:.4f}`\n"
    return message

async def main():
    # Запуск основной функции бота
    logger.debug("Запуск основной функции main()")
    import src.config as config
    check_tokens(config, logger)

    bot = telebot.TeleBot(TELEGRAM_TOKEN, state_storage=StateMemoryStorage())
    register_handlers(bot, config, logger)
    setup_bot_commands(bot)

    exchange = ccxt.bybit({'enableRateLimit': True})
    last_signals_7_30 = {}
    last_signals_9_20_rsi = {}
    error_count = 0
    last_status_time = datetime.now(timezone.utc)
    last_ping_time = datetime.now(timezone.utc)

    try:
        # Проверка доступности торговых пар
        global SYMBOLS
        symbols = await validate_symbols(exchange, SYMBOLS)
        logger.info(f'Доступные пары: {symbols}')
        send_message(bot, TELEGRAM_CHAT_ID, f'✅ *Бот запущен*: Проверены торговые пары ({len(symbols)})', logger)
    except Exception as e:
        error_msg = f'❌ *Критическая ошибка*: Не удалось загрузить торговые пары: {e}'
        logger.critical(error_msg)
        send_message(bot, TELEGRAM_CHAT_ID, error_msg, logger)
        await exchange.close()
        return

    try:
        # Тестовый запрос к бирже
        test_symbol = symbols[0]
        await get_ohlcv(exchange, test_symbol, log_candles=True)
        logger.info(f'Успешный тестовый запрос для {test_symbol}')
        send_message(bot, TELEGRAM_CHAT_ID, f'✅ *Бот запущен*: Успешный тестовый запрос для {test_symbol}', logger)
    except Exception as e:
        error_msg = f'❌ *Критическая ошибка*: Не удалось подключиться к бирже: {e}'
        logger.critical(error_msg)
        send_message(bot, TELEGRAM_CHAT_ID, error_msg, logger)
        await exchange.close()
        return

    logger.info('Бот сигналов EMA (две стратегии) запущен.')

    while True:
        try:
            # Непрерывная проверка сигналов с задержкой 15 минут
            logger.debug("Начало обработки всех торговых пар")
            tasks = [get_ohlcv(exchange, symbol, log_candles=True) for symbol in symbols]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            logger.debug("Завершено получение данных для всех пар")

            success_count = 0
            for symbol, result in zip(symbols, results):
                if isinstance(result, Exception):
                    logger.error(f'Ошибка обработки {symbol}: {result}')
                    continue
                success_count += 1
                df = result
                price = df['close'].iloc[-1]

                # Проверка сигнала по стратегии EMA7/EMA30
                signal_7_30 = check_signal_ema7_30(df, LIMIT)
                if signal_7_30 and last_signals_7_30.get(symbol) != signal_7_30:
                    tp, sl_tr, sl_mkt = calc_oco_prices(signal_7_30, price)
                    message = format_signal_message(
                        symbol, f"EMA7/30 {signal_7_30}", price, tp, sl_tr, sl_mkt, TIMEFRAME
                    )
                    send_message(bot, TELEGRAM_CHAT_ID, message, logger)
                    logger.info(f'EMA7/30 — {symbol}: {signal_7_30} (цена {price})')
                    last_signals_7_30[symbol] = signal_7_30

                # Проверка сигнала по стратегии EMA9/EMA20 + RSI
                signal_9_20_rsi = check_signal_ema9_20_rsi(df, LIMIT)
                if signal_9_20_rsi and last_signals_9_20_rsi.get(symbol) != signal_9_20_rsi:
                    tp, sl_tr, sl_mkt = calc_oco_prices(signal_9_20_rsi, price)
                    message = format_signal_message(
                        symbol, f"EMA9/20+RSI {signal_9_20_rsi}", price, tp, sl_tr, sl_mkt,
                        TIMEFRAME, (df['rsi'].iloc[-2], df['rsi'].iloc[-1])
                    )
                    send_message(bot, TELEGRAM_CHAT_ID, message, logger)
                    logger.info(
                        f'EMA9/20+RSI — {symbol}: {signal_9_20_rsi} (цена {price}) '
                        f'RSI: {df["rsi"].iloc[-2]:.1f}→{df["rsi"].iloc[-1]:.1f}'
                    )
                    last_signals_9_20_rsi[symbol] = signal_9_20_rsi

            logger.info(f'Успешно обработано {success_count}/{len(symbols)} пар')

            if success_count > 0:
                error_count = 0

            # Отправка периодического статуса
            now = datetime.now(timezone.utc)
            if (now - last_status_time).total_seconds() >= STATUS_INTERVAL:
                send_message(bot, TELEGRAM_CHAT_ID, f'🔔 *Статус бота*: Обработано {success_count}/{len(symbols)} пар', logger)
                logger.info("Отправлен статус бота")
                last_status_time = now

            # Отправка периодического пинга
            if (now - last_ping_time).total_seconds() >= PING_INTERVAL:
                send_message(bot, TELEGRAM_CHAT_ID, '🔔 *Бот сигналов EMA*: Работает!', logger)
                logger.info('Отправлено пинг-сообщение (6ч)')
                last_ping_time = now

            # Логирование ожидания в старом формате
            next_check = now + timedelta(seconds=900)
            wait_seconds = (next_check - now).total_seconds()
            logger.debug(f'Ожидание {wait_seconds:.1f} секунд до следующей проверки в {next_check.strftime("%Y-%m-%d %H:%M:%S")}+00:00')

            # Задержка 15 минут
            await asyncio.sleep(900)

        except Exception as error:
            # Обработка критических ошибок
            error_count += 1
            logger.error(f'Критическая ошибка в цикле: {error}')
            if error_count >= ERROR_THRESHOLD:
                error_msg = f'❌ *Критическая ошибка*: Бот остановлен из-за повторяющихся сбоев: {error}'
                send_message(bot, TELEGRAM_CHAT_ID, error_msg, logger)
                logger.critical('Бот остановлен из-за превышения порога ошибок')
                break

    # Закрытие соединения с биржей
    await exchange.close()
    logger.debug("Биржа закрыта")
    