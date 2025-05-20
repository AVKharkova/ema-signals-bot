from telebot.handler_backends import State, StatesGroup
from telebot.types import BotCommand
import logging

# Настройка логирования
logger = logging.getLogger(__name__)

class SettingsState(StatesGroup):
    waiting_for_tp = State()  # Состояние ожидания ввода тейк-профита
    waiting_for_sl = State()  # Состояние ожидания ввода стоп-лосса

def register_handlers(bot, config, logger):
    @bot.message_handler(commands=['help'])
    async def send_help(message):
        # Обработка команды /help для отображения доступных команд
        logger.debug(f"Получена команда /help от chat_id: {message.chat.id}")
        if str(message.chat.id) != config.TELEGRAM_CHAT_ID:
            await bot.reply_to(message, "Несанкционированный доступ.")
            logger.warning(f"Несанкционированный доступ: chat_id {message.chat.id} != {config.TELEGRAM_CHAT_ID}")
            return
        help_text = (
            "**📋 Доступные команды бота**\n\n"
            "`/help` - Показать этот список команд\n"
            # Убраны команды /set_tp и /set_sl из списка
        )
        await bot.reply_to(message, help_text, parse_mode='Markdown')
        logger.info("Команда /help обработана")

    @bot.message_handler(commands=['set_tp'])
    async def set_take_profit(message):
        # Обработка команды /set_tp отключена
        logger.debug(f"Попытка вызова /set_tp от chat_id: {message.chat.id}")
        if str(message.chat.id) != config.TELEGRAM_CHAT_ID:
            await bot.reply_to(message, "Несанкционированный доступ.")
            logger.warning(f"Несанкционированный доступ: chat_id {message.chat.id} != {config.TELEGRAM_CHAT_ID}")
            return
        await bot.reply_to(message, "Изменение тейк-профита через Telegram отключено.")
        logger.info("Попытка изменения тейк-профита заблокирована")

    @bot.message_handler(state=SettingsState.waiting_for_tp)
    async def process_tp(message):
        # Обработка ввода тейк-профита отключена
        logger.debug(f"Попытка ввода тейк-профита: {message.text}")
        await bot.reply_to(message, "Изменение тейк-профита через Telegram отключено.")
        logger.info("Попытка ввода тейк-профита заблокирована")
        await bot.delete_state(message.from_user.id, message.chat.id)

    @bot.message_handler(commands=['set_sl'])
    async def set_stop_loss(message):
        # Обработка команды /set_sl отключена
        logger.debug(f"Попытка вызова /set_sl от chat_id: {message.chat.id}")
        if str(message.chat.id) != config.TELEGRAM_CHAT_ID:
            await bot.reply_to(message, "Несанкционированный доступ.")
            logger.warning(f"Несанкционированный доступ: chat_id {message.chat.id} != {config.TELEGRAM_CHAT_ID}")
            return
        await bot.reply_to(message, "Изменение стоп-лосса через Telegram отключено.")
        logger.info("Попытка изменения стоп-лосса заблокирована")

    @bot.message_handler(state=SettingsState.waiting_for_sl)
    async def process_sl(message):
        # Обработка ввода стоп-лосса отключена
        logger.debug(f"Попытка ввода стоп-лосса: {message.text}")
        await bot.reply_to(message, "Изменение стоп-лосса через Telegram отключено.")
        logger.info("Попытка ввода стоп-лосса заблокирована")
        await bot.delete_state(message.from_user.id, message.chat.id)

def setup_bot_commands(bot):
    # Регистрация только команды /help
    commands = [
        BotCommand("help", "Показать список доступных команд")
    ]
    bot.set_my_commands(commands)
