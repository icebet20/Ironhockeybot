import os
import logging
import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot, Update, InputFile
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------- env ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL = os.getenv("CHANNEL_USERNAME")            # e.g. @kfkfkjfjfc
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))         # your numeric Telegram ID (via @userinfobot)
TIMEZONE_OFFSET = int(os.getenv("TZ_OFFSET", "3")) # Moscow = +3
WELCOME_ON_START = os.getenv("WELCOME_ON_START", "true").lower() == "true"

# ---------- logging ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("iron-hockey")

# ---------- helpers ----------
async def send_photo(bot: Bot, photo_path_or_url: str, caption: str = ""):
    if photo_path_or_url.startswith("http"):
        await bot.send_photo(CHANNEL, photo=photo_path_or_url, caption=caption, parse_mode=ParseMode.MARKDOWN)
    else:
        with open(photo_path_or_url, "rb") as f:
            await bot.send_photo(CHANNEL, photo=InputFile(f), caption=caption, parse_mode=ParseMode.MARKDOWN)

async def send_text(bot: Bot, text: str):
    await bot.send_message(CHANNEL, text, parse_mode=ParseMode.MARKDOWN)

# ---------- commands for ADMIN only ----------
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else 0
        if uid != ADMIN_ID:
            return
        return await func(update, context)
    return wrapper

@admin_only
async def post_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /post <text> — постит текст
    /postphoto <url> | <подпись> — постит фото (url или файл в ответе на сообщение с изображением)
    """
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Формат: /post ТЕКСТ")
        return
    await send_text(context.bot, text)
    await update.message.reply_text("✅ Отправлено в канал")

@admin_only
async def postphoto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    argline = " ".join(context.args)
    if "|" in argline:
        url, caption = [a.strip() for a in argline.split("|", 1)]
    else:
        url, caption = argline.strip(), ""
    # если фото прислано файлом в реплае
    if not url and update.message.reply_to_message and update.message.reply_to_message.photo:
        file_id = update.message.reply_to_message.photo[-1].file_id
        await context.bot.send_photo(CHANNEL, photo=file_id, caption=caption, parse_mode=ParseMode.MARKDOWN)
    else:
        await send_photo(context.bot, url, caption)
    await update.message.reply_text("✅ Фото отправлено в канал")

@admin_only
async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /schedule HH:MM | ТЕКСТ — запланировать текст
    /schedulephoto HH:MM | URL | ПОДПИСЬ — запланировать фото
    Время по МСК (TZ_OFFSET).
    """
    line = " ".join(context.args)
    if "|" not in line:
        await update.message.reply_text("Формат: /schedule 18:00 | ТЕКСТ")
        return
    time_str, payload = [p.strip() for p in line.split("|", 1)]
    try:
        now = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
        hh, mm = map(int, time_str.split(":"))
        run_local = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if run_local <= now:
            run_local += timedelta(days=1)
        run_utc = run_local - timedelta(hours=TIMEZONE_OFFSET)
    except Exception:
        await update.message.reply_text("Некорректное время. Пример: 18:30")
        return

    job_id = f"txt-{run_utc.isoformat()}"
    context.job_queue.run_once(
        lambda c: c.bot.send_message(CHANNEL, payload, parse_mode=ParseMode.MARKDOWN),
        when=(run_utc - datetime.utcnow()).total_seconds(),
        name=job_id,
        chat_id=update.effective_chat.id
    )
    await update.message.reply_text(f"🗓 Запланировано {time_str} МСК")

@admin_only
async def schedulephoto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    line = " ".join(context.args)
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 2:
        await update.message.reply_text("Формат: /schedulephoto 18:00 | URL | ПОДПИСЬ")
        return
    time_str, url = parts[0], parts[1]
    caption = parts[2] if len(parts) > 2 else ""

    try:
        now = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
        hh, mm = map(int, time_str.split(":"))
        run_local = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if run_local <= now:
            run_local += timedelta(days=1)
        run_utc = run_local - timedelta(hours=TIMEZONE_OFFSET)
    except Exception:
        await update.message.reply_text("Некорректное время. Пример: 18:30")
        return

    async def _job(ctx: ContextTypes.DEFAULT_TYPE):
        await send_photo(ctx.bot, url, caption)

    # через APScheduler (надежнее для Railway)
    scheduler: AsyncIOScheduler = update.application.job_queue.scheduler  # type: ignore
    scheduler.add_job(lambda: asyncio.create_task(_job(context)),
                      "date", run_date=run_utc)

    await update.message.reply_text(f"🗓 Фото запланировано {time_str} МСК")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user and update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("Бот готов. Команды: /post, /postphoto, /schedule, /schedulephoto")
    else:
        await update.message.reply_text("Привет!")

async def main():
    if not BOT_TOKEN or not CHANNEL:
        raise SystemExit("BOT_TOKEN и CHANNEL_USERNAME обязательны")
    app = Application.builder().token(BOT_TOKEN).build()

    # Встроенный планировщик (для schedulephoto)
    scheduler = AsyncIOScheduler()
    scheduler.start()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("post", post_cmd))
    app.add_handler(CommandHandler("postphoto", postphoto_cmd))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(CommandHandler("schedulephoto", schedulephoto_cmd))
    # игнор остальных сообщений
    app.add_handler(MessageHandler(filters.ALL, lambda u, c: None))

    if WELCOME_ON_START and ADMIN_ID:
        try:
            await app.bot.send_message(ADMIN_ID, "✅ Бот запущен. /post /schedule готовы.")
        except Exception as e:
            log.warning(f"notify admin failed: {e}")

    await app.initialize()
    await app.start()
    log.info("IRON HOCKEY bot running…")
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await app.updater.wait()

if __name__ == "__main__":
    asyncio.run(main())
