import logging
import os
import sys
import asyncio
import threading
from datetime import datetime, timedelta, timezone
import time
import ccxt.async_support as ccxt
import pandas as pd
from dotenv import load_dotenv
import telebot
from telebot.handler_backends import State, StatesGroup
from telebot.storage import StateMemoryStorage
from telebot.types import BotCommand

# ----- НАСТРОЙКА ЛОГГИРОВАНИЯ -----
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Настройка вывода логов в консоль
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setLevel(logging.DEBUG)
stream_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(levelname)s - %(message)s - %(name)s')
)
logger.addHandler(stream_handler)

# Настройка записи логов в файл
file_handler = logging.FileHandler('ema_signals.log', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(levelname)s - %(message)s - %(name)s')
)
logger.addHandler(file_handler)

# ----- ПАРАМЕТРЫ КОНФИГУРАЦИИ -----
load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')  # Токен Telegram-бота
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')  # ID чата для отправки сообщений

# Список торговых пар для мониторинга
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT',
    'XRP/USDT', 'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT',
    'DOT/USDT', 'TRX/USDT', 'LINK/USDT', 'SHIB/USDT',
    'UNI/USDT', 'XLM/USDT', 'ATOM/USDT', 'ICP/USDT',
    'FIL/USDT', 'APT/USDT', 'NEAR/USDT', 'ARB/USDT',
    'SUI/USDT', 'AAVE/USDT', 'OP/USDT', 'PEPE/USDT',
    'STX/USDT', 'APE/USDT', 'MOCA/USDT', 'POL/USDT',
    'ZK/USDT', 'ONDO/USDT'
]

# Параметры торговли
TIMEFRAME = '1h'  # Таймфрейм графика
LIMIT = 150  # Количество свечей для анализа
RETRY_PERIOD = 3600  # Период повтора в секундах (1 час)
ERROR_THRESHOLD = 5  # Максимальное количество ошибок перед уведомлением
STATUS_INTERVAL = 86400  # Интервал отправки статуса (1 день, в секундах)
PING_INTERVAL = 6 * 3600  # Интервал пинга (6 часов, в секундах)
MIN_WAIT_SECONDS = 10  # Минимальное время ожидания для синхронизации

# Загрузка процентов тейк-профита и стоп-лосса из переменных окружения (как float с дефолтом)
TP_PERCENT = float(os.getenv('TAKE_PROFIT_PERCENT', '2.0'))  # Процент тейк-профита
SL_PERCENT = float(os.getenv('STOP_LOSS_PERCENT', '1.0'))  # Процент стоп-лосса

# ----- КАСТОМНЫЕ ИСКЛЮЧЕНИЯ -----
class MissingTokenError(Exception):
    """Исключение, вызываемое при отсутствии или неверном формате переменных окружения."""
    pass

# ----- НАСТРОЙКА TELEGRAM-БОТА С УПРАВЛЕНИЕМ СОСТОЯНИЯМИ -----
class SettingsState(StatesGroup):
    """Класс для управления состояниями ввода команд Telegram."""
    waiting_for_tp = State()  # Ожидание ввода тейк-профита
    waiting_for_sl = State()  # Ожидание ввода стоп-лосса

bot = telebot.TeleBot(TELEGRAM_TOKEN, state_storage=StateMemoryStorage())

# ----- СПИСОК КОМАНД ДЛЯ TELEGRAM -----
def setup_bot_commands():
    """Регистрирует команды бота в Telegram с описаниями."""
    commands = [
        BotCommand("help", "Показать список доступных команд"),
        BotCommand("set_tp", "Установить процент тейк-профита"),
        BotCommand("set_sl", "Установить процент стоп-лосса")
    ]
    try:
        bot.set_my_commands(commands)
        logger.info("Команды бота успешно зарегистрированы в Telegram")
    except Exception as e:
        logger.error(f"Ошибка регистрации команд бота: {e}")

# ----- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -----
def check_tokens():
    """Проверяет наличие и корректность всех переменных окружения."""
    logger.debug("Проверка переменных окружения")
    required_vars = {
        'TELEGRAM_TOKEN': TELEGRAM_TOKEN,
        'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID,
        'TAKE_PROFIT_PERCENT': TP_PERCENT,
        'STOP_LOSS_PERCENT': SL_PERCENT
    }
    missing = [key for key, value in required_vars.items() if not value]
    if missing:
        error_msg = f'Бот остановлен. Отсутствуют переменные окружения: {", ".join(missing)}'
        logger.critical(error_msg)
        raise MissingTokenError(error_msg)
    try:
        int(TELEGRAM_CHAT_ID)
        logger.debug(f"TELEGRAM_CHAT_ID корректен: {TELEGRAM_CHAT_ID}")
    except ValueError:
        error_msg = 'TELEGRAM_CHAT_ID должен быть числом.'
        logger.critical(error_msg)
        raise MissingTokenError(error_msg)
    try:
        tp = float(TP_PERCENT)
        if tp <= 0:
            raise ValueError("TAKE_PROFIT_PERCENT должен быть положительным числом")
        logger.debug(f"TAKE_PROFIT_PERCENT корректен: {TP_PERCENT}")
    except ValueError as e:
        error_msg = f'Неверный формат TAKE_PROFIT_PERCENT: {e}'
        logger.critical(error_msg)
        raise MissingTokenError(error_msg)
    try:
        sl = float(SL_PERCENT)
        if sl <= 0:
            raise ValueError("STOP_LOSS_PERCENT должен быть положительным числом")
        logger.debug(f"STOP_LOSS_PERCENT корректен: {SL_PERCENT}")
    except ValueError as e:
        error_msg = f'Неверный формат STOP_LOSS_PERCENT: {e}'
        logger.critical(error_msg)
        raise MissingTokenError(error_msg)

def calc_oco_prices(direction, price, tp_perc=TP_PERCENT, sl_perc=SL_PERCENT):
    logger.debug(f"Расчёт OCO для {direction}, цена: {price}, TP%: {tp_perc}, SL%: {sl_perc}")
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
    logger.debug(f"Сформировано сообщение: {message}")
    return message

def send_message(message):
    logger.debug(f'Попытка отправки сообщения: {message}')
    try:
        bot.send_message(TELEGRAM_CHAT_ID, message, parse_mode='Markdown')
        logger.info('Сообщение успешно отправлено в Telegram.')
    except Exception as e:
        logger.error(f'Ошибка отправки сообщения в Telegram: {e}')

def send_critical_message(msg):
    logger.debug(f'Попытка отправки критического сообщения: {msg}')
    try:
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            bot2 = telebot.TeleBot(TELEGRAM_TOKEN)
            bot2.send_message(TELEGRAM_CHAT_ID, msg, parse_mode='Markdown')
            logger.info('Критическое сообщение отправлено.')
    except Exception as e:
        logger.error(f'Ошибка отправки аварийного уведомления: {e}')

async def get_ohlcv(exchange, symbol):
    logger.debug(f'Получение OHLCV для {symbol}')
    try:
        data = await asyncio.wait_for(
            exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=LIMIT),
            timeout=30.0  # Тайм-аут 30 секунд
        )
        if not data:
            logger.error(f'Нет данных для {symbol}')
            raise ValueError(f'Нет данных для {symbol}')
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
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

def calc_ema(df, period):
    logger.debug(f"Расчёт EMA с периодом {period}")
    if period <= 0:
        raise ValueError('Период EMA должен быть положительным')
    return df['close'].ewm(span=period, adjust=False).mean()

def calc_rsi(df, period=14):
    logger.debug(f"Расчёт RSI с периодом {period}")
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_gain = up.rolling(window=period, min_periods=period).mean()
    avg_loss = down.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.where(avg_loss != 0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return rsi

def check_signal_ema7_30(df):
    logger.debug("Проверка сигнала EMA7/EMA30")
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
        logger.info("Обнаружен LONG сигнал по EMA7/EMA30")
        return "LONG"
    elif (
        df['ema7'].iloc[-2] > df['ema30'].iloc[-2] and
        df['ema7'].iloc[-1] < df['ema30'].iloc[-1] and
        df['close'].iloc[-1] < df['ema7'].iloc[-1] and
        df['close'].iloc[-1] < df['ema30'].iloc[-1]
    ):
        logger.info("Обнаружен SHORT сигнал по EMA7/EMA30")
        return "SHORT"
    return None

def check_signal_ema9_20_rsi(df):
    logger.debug("Проверка сигнала EMA9/EMA20+RSI")
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
        logger.info("Обнаружен LONG (RSI) сигнал по EMA9/EMA20")
        return "LONG (RSI)"
    elif (
        df['ema9'].iloc[-2] > df['ema20'].iloc[-2] and
        df['ema9'].iloc[-1] < df['ema20'].iloc[-1] and
        df['rsi'].iloc[-2] < 45 and
        df['rsi'].iloc[-1] >= 45
    ):
        logger.info("Обнаружен SHORT (RSI) сигнал по EMA9/EMA20")
        return "SHORT (RSI)"
    return None

# ----- ОБРАБОТЧИКИ КОМАНД TELEGRAM -----
@bot.message_handler(commands=['help'])
def send_help(message):
    logger.debug(f"Получена команда /help от chat_id: {message.chat.id}")
    if str(message.chat.id) != TELEGRAM_CHAT_ID:
        bot.reply_to(message, "Несанкционированный доступ.")
        logger.warning(f"Несанкционированный доступ: chat_id {message.chat.id} != {TELEGRAM_CHAT_ID}")
        return
    help_text = (
        "**📋 Доступные команды бота**\n\n"
        "`/help` - Показать этот список команд\n"
        "`/set_tp` - Установить процент тейк-профита (например, 2.5)\n"
        "`/set_sl` - Установить процент стоп-лосса (например, 1.0)"
    )
    bot.reply_to(message, help_text, parse_mode='Markdown')
    logger.info("Команда /help обработана")

@bot.message_handler(commands=['set_tp'])
def set_take_profit(message):
    logger.debug(f"Получена команда /set_tp от chat_id: {message.chat.id}")
    if str(message.chat.id) != TELEGRAM_CHAT_ID:
        bot.reply_to(message, "Несанкционированный доступ.")
        logger.warning(f"Несанкционированный доступ: chat_id {message.chat.id} != {TELEGRAM_CHAT_ID}")
        return
    bot.set_state(message.from_user.id, SettingsState.waiting_for_tp, message.chat.id)
    bot.reply_to(message, "Введите новый процент тейк-профита (например, 2.5):")
    logger.info("Запрошено изменение тейк-профита")

@bot.message_handler(state=SettingsState.waiting_for_tp)
def process_tp(message):
    logger.debug(f"Получен ввод тейк-профита: {message.text}")
    try:
        new_tp = float(message.text)
        if new_tp <= 0:
            raise ValueError("Тейк-профит должен быть положительным")
        global TP_PERCENT
        TP_PERCENT = new_tp
        bot.reply_to(message, f"Тейк-профит обновлен до {new_tp}%")
        logger.info(f"Тейк-профит обновлен до {new_tp}% через Telegram")
        bot.delete_state(message.from_user.id, message.chat.id)
    except ValueError:
        bot.reply_to(message, "Неверный ввод. Введите положительное число (например, 2.5).")
        logger.error(f"Неверный ввод тейк-профита: {message.text}")

@bot.message_handler(commands=['set_sl'])
def set_stop_loss(message):
    logger.debug(f"Получена команда /set_sl от chat_id: {message.chat.id}")
    if str(message.chat.id) != TELEGRAM_CHAT_ID:
        bot.reply_to(message, "Несанкционированный доступ.")
        logger.warning(f"Несанкционированный доступ: chat_id {message.chat.id} != {TELEGRAM_CHAT_ID}")
        return
    bot.set_state(message.from_user.id, SettingsState.waiting_for_sl, message.chat.id)
    bot.reply_to(message, "Введите новый процент стоп-лосса (например, 1.0):")
    logger.info("Запрошено изменение стоп-лосса")

@bot.message_handler(state=SettingsState.waiting_for_sl)
def process_sl(message):
    logger.debug(f"Получен ввод стоп-лосса: {message.text}")
    try:
        new_sl = float(message.text)
        if new_sl <= 0:
            raise ValueError("Стоп-лосс должен быть положительным")
        global SL_PERCENT
        SL_PERCENT = new_sl
        bot.reply_to(message, f"Стоп-лосс обновлен до {new_sl}%")
        logger.info(f"Стоп-лосс обновлен до {new_sl}% через Telegram")
        bot.delete_state(message.from_user.id, message.chat.id)
    except ValueError:
        bot.reply_to(message, "Неверный ввод. Введите положительное число (например, 1.0).")
        logger.error(f"Неверный ввод стоп-лосса: {message.text}")

async def main():
    logger.debug("Запуск основной функции main()")
    check_tokens()
    exchange = ccxt.bybit({'enableRateLimit': True})  # Инициализация биржи Bybit
    logger.debug("Инициализирована биржа Bybit")
    last_signals_7_30 = {}
    last_signals_9_20_rsi = {}
    error_count = 0
    last_status_time = datetime.now(timezone.utc)
    last_ping_time = datetime.now(timezone.utc)
    setup_bot_commands()

    # Убрана строка с infinity_polling!

    try:
        global SYMBOLS
        SYMBOLS = await validate_symbols(exchange, SYMBOLS)
        logger.info(f'Доступные пары: {SYMBOLS}')
        send_message(f'✅ *Бот запущен*: Проверены торговые пары ({len(SYMBOLS)})')
    except Exception as e:
        error_msg = f'❌ *Критическая ошибка*: Не удалось загрузить торговые пары: {e}'
        logger.critical(error_msg)
        send_message(error_msg)
        await exchange.close()
        return

    try:
        test_symbol = SYMBOLS[0]
        await get_ohlcv(exchange, test_symbol)
        logger.info(f'Успешный тестовый запрос для {test_symbol}')
        send_message(f'✅ *Бот запущен*: Успешный тестовый запрос для {test_symbol}')
    except Exception as e:
        error_msg = f'❌ *Критическая ошибка*: Не удалось подключиться к бирже: {e}'
        logger.critical(error_msg)
        send_message(error_msg)
        await exchange.close()
        return

    logger.info('Бот сигналов EMA (две стратегии) запущен.')

    while True:
        try:
            now = datetime.now(timezone.utc)
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            wait_seconds = max((next_hour - now).total_seconds(), MIN_WAIT_SECONDS)
            logger.debug(f'Ожидание {wait_seconds:.1f} секунд до следующей проверки в {next_hour}')
            await asyncio.sleep(wait_seconds)
            logger.debug("Начало обработки торговых пар")
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
                signal_7_30 = check_signal_ema7_30(df)
                if signal_7_30 and last_signals_7_30.get(symbol) != signal_7_30:
                    tp, sl_tr, sl_mkt = calc_oco_prices(signal_7_30, price)
                    message = format_signal_message(
                        symbol, f"EMA7/30 {signal_7_30}", price, tp, sl_tr, sl_mkt, TIMEFRAME
                    )
                    send_message(message)
                    logger.info(f'EMA7/30 — {symbol}: {signal_7_30} (цена {price})')
                    last_signals_7_30[symbol] = signal_7_30
                signal_9_20_rsi = check_signal_ema9_20_rsi(df)
                if signal_9_20_rsi and last_signals_9_20_rsi.get(symbol) != signal_9_20_rsi:
                    tp, sl_tr, sl_mkt = calc_oco_prices(signal_9_20_rsi, price)
                    message = format_signal_message(
                        symbol, f"EMA9/20+RSI {signal_9_20_rsi}", price, tp, sl_tr, sl_mkt,
                        TIMEFRAME, (df['rsi'].iloc[-2], df['rsi'].iloc[-1])
                    )
                    send_message(message)
                    logger.info(
                        f'EMA9/20+RSI — {symbol}: {signal_9_20_rsi} (цена {price}) '
                        f'RSI: {df["rsi"].iloc[-2]:.1f}→{df["rsi"].iloc[-1]:.1f}'
                    )
                    last_signals_9_20_rsi[symbol] = signal_9_20_rsi
            logger.info(f'Успешно обработано {success_count}/{len(SYMBOLS)} пар')
            if success_count > 0:
                error_count = 0
            now = datetime.now(timezone.utc)
            if (now - last_status_time).total_seconds() >= STATUS_INTERVAL:
                send_message(f'🔔 *Статус бота*: Обработано {success_count}/{len(SYMBOLS)} пар')
                logger.info("Отправлен статус бота")
                last_status_time = now
            if (now - last_ping_time).total_seconds() >= PING_INTERVAL:
                send_message('🔔 *Бот сигналов EMA*: Работает!')
                logger.info('Отправлено пинг-сообщение (6ч)')
                last_ping_time = now
        except Exception as error:
            error_count += 1
            logger.error(f'Критическая ошибка: {error}')
            if error_count >= ERROR_THRESHOLD:
                error_msg = f'❌ *Критическая ошибка*: Бот остановлен из-за повторяющихся сбоев: {error}'
                send_message(error_msg)
                logger.critical('Бот остановлен из-за превышения порога ошибок')
                break
    await exchange.close()
    logger.debug("Биржа закрыта")

# ----- СТАРТ TELEGRAM POLLING В ОТДЕЛЬНОМ ПОТОКЕ -----
def run_bot():
    bot.infinity_polling()

if __name__ == '__main__':
    logger.debug("Запуск программы")
    threading.Thread(target=run_bot, daemon=True).start()
    while True:
        try:
            asyncio.run(main())
            break
        except KeyboardInterrupt:
            logger.info('Бот остановлен вручную.')
            send_critical_message('⚠️ *Бот EMA*: Остановлен вручную (KeyboardInterrupt)')
            break
        except Exception as e:
            err_text = f'❌ *КРИТИЧЕСКАЯ ОШИБКА* (фатальный сбой): {e}'
            logger.critical(err_text, exc_info=True)
            send_critical_message(err_text)
            time.sleep(60)
