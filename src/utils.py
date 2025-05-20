import logging
import sys

def setup_logging():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.DEBUG)
    # Убираем указание модуля из формата логов
    stream_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    )
    logger.addHandler(stream_handler)
    file_handler = logging.FileHandler('ema_signals.log', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    )
    logger.addHandler(file_handler)
    return logger

class MissingTokenError(Exception):
    pass

def check_tokens(config, logger):
    required_vars = {
        'TELEGRAM_TOKEN': config.TELEGRAM_TOKEN,
        'TELEGRAM_CHAT_ID': config.TELEGRAM_CHAT_ID,
        'TAKE_PROFIT_PERCENT': config.TP_PERCENT,
        'STOP_LOSS_PERCENT': config.SL_PERCENT
    }
    missing = [key for key, value in required_vars.items() if not value]
    if missing:
        error_msg = f'Бот остановлен. Отсутствуют переменные окружения: {", ".join(missing)}'
        logger.critical(error_msg)
        raise MissingTokenError(error_msg)
    try:
        int(config.TELEGRAM_CHAT_ID)
        # Убрано логирование TELEGRAM_CHAT_ID
    except ValueError:
        error_msg = 'TELEGRAM_CHAT_ID должен быть числом.'
        logger.critical(error_msg)
        raise MissingTokenError(error_msg)
    if config.TP_PERCENT <= 0 or config.SL_PERCENT <= 0:
        error_msg = 'TP/SL должны быть положительными числами.'
        logger.critical(error_msg)
        raise MissingTokenError(error_msg)

def send_message(bot, chat_id, message, logger):
    try:
        bot.send_message(chat_id, message, parse_mode='Markdown')
        logger.info('Сообщение отправлено в Telegram.')
    except Exception as e:
        logger.error(f'Ошибка отправки сообщения в Telegram: {e}')

def send_critical_message(bot, chat_id, msg, logger):
    try:
        bot.send_message(chat_id, msg, parse_mode='Markdown')
        logger.info('Критическое сообщение отправлено.')
    except Exception as e:
        logger.error(f'Ошибка отправки аварийного уведомления: {e}')
