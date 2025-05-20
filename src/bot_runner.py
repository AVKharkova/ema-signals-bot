import threading

def run_bot(bot):
    thread = threading.Thread(target=bot.infinity_polling, daemon=True)
    thread.start()
