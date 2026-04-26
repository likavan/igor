from datetime import time
from telegram import BotCommand
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, CallbackQueryHandler, filters
from config import TELEGRAM_TOKEN, TZ
from db import init_db
from handlers import (
    handle_message, handle_callback, help_command, start_command,
    list_reminders, delete_reminder,
    list_todos, todo_done, todo_delete, todo_edit,
    check_emails, check_new_emails,
    check_reminders, check_emails_periodic, morning_summary,
)


async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("e", "Posledné emaily"),
        BotCommand("en", "Nové neprečítané emaily"),
        BotCommand("r", "Zoznam pripomienok"),
        BotCommand("d", "Zmazať pripomienku (/d 3)"),
        BotCommand("t", "Zoznam úloh"),
        BotCommand("td", "Splniť úlohu (/td 3)"),
        BotCommand("te", "Upraviť úlohu (/te 3 text)"),
        BotCommand("tx", "Vymazať úlohu (/tx 3)"),
        BotCommand("h", "Pomoc"),
        BotCommand("start", "Reset menu"),
    ])


def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("h", help_command))
    app.add_handler(CommandHandler("reminders", list_reminders))
    app.add_handler(CommandHandler("r", list_reminders))
    app.add_handler(CommandHandler("delete", delete_reminder))
    app.add_handler(CommandHandler("d", delete_reminder))
    app.add_handler(CommandHandler("emails", check_emails))
    app.add_handler(CommandHandler("e", check_emails))
    app.add_handler(CommandHandler("en", check_new_emails))
    app.add_handler(CommandHandler("t", list_todos))
    app.add_handler(CommandHandler("td", todo_done))
    app.add_handler(CommandHandler("te", todo_edit))
    app.add_handler(CommandHandler("tx", todo_delete))
    app.add_handler(CallbackQueryHandler(handle_callback))

    app.job_queue.run_repeating(check_reminders, interval=60, first=10)
    app.job_queue.run_repeating(check_emails_periodic, interval=3600, first=60)
    app.job_queue.run_daily(morning_summary, time=time(hour=8, minute=0, tzinfo=TZ))

    print("Bot beží...")
    app.run_polling()


if __name__ == "__main__":
    main()
