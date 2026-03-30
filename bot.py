import asyncio
import logging
import json
import os
import re
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

from tiktok_scraper import search_tiktok_by_hashtag, check_hashtag_exists

# ─── Config (хранится в памяти, сохраняется в файл) ──────────────
CONFIG_FILE = "config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

# ─── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Conversation states ─────────────────────────────────────────
WAITING_HASHTAG, WAITING_LIKES = range(2)

# ─── Bot commands ─────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 Привет! Я бот для поиска TikTok-видео.\n\n"
        "📋 Команды:\n"
        "/add — добавить хэштег\n"
        "/remove — удалить хэштег\n"
        "/list — список отслеживаемых хэштегов\n"
        "/search — найти видео прямо сейчас\n"
        "/interval — изменить интервал проверки (в минутах)\n"
        "/help — помощь"
    )
    await update.message.reply_text(text)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔧 Как пользоваться:\n\n"
        "1. Добавьте хэштег командой /add\n"
        "2. Укажите минимум лайков\n"
        "3. Бот будет автоматически проверять TikTok\n"
        "   и присылать видео, которые набрали нужное\n"
        "   количество лайков\n\n"
        "По умолчанию проверка каждые 60 минут.\n"
        "Измените командой /interval"
    )
    await update.message.reply_text(text)


# ─── /add — добавить хэштег ──────────────────────────────────────

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введите хэштег (с # или без):\n"
        "Например: #funny или funny"
    )
    return WAITING_HASHTAG


async def add_hashtag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().lstrip("#").lower()
    hashtag = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_]", "", raw)

    if not hashtag:
        await update.message.reply_text("❌ Некорректный хэштег. Попробуйте снова:")
        return WAITING_HASHTAG

    await update.message.reply_text(f"🔍 Проверяю хэштег #{hashtag}...")

    exists = await check_hashtag_exists(hashtag)
    if not exists:
        await update.message.reply_text(
            f"❌ Хэштег #{hashtag} не найден в TikTok.\n"
            "Попробуйте другой:"
        )
        return WAITING_HASHTAG

    context.user_data["pending_hashtag"] = hashtag
    await update.message.reply_text(
        f"✅ Хэштег #{hashtag} существует!\n\n"
        "Теперь введите минимальное количество лайков:\n"
        "Например: 1000"
    )
    return WAITING_LIKES


async def add_likes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        min_likes = int(update.message.text.strip())
        if min_likes < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введите число (например: 1000):")
        return WAITING_LIKES

    hashtag = context.user_data.pop("pending_hashtag")
    chat_id = str(update.effective_chat.id)

    config = load_config()
    if chat_id not in config:
        config[chat_id] = {"hashtags": {}, "interval_minutes": 60}
    config[chat_id]["hashtags"][hashtag] = {
        "min_likes": min_likes,
        "last_sent_ids": []
    }
    save_config(config)

    await update.message.reply_text(
        f"✅ Готово!\n\n"
        f"Хэштег: #{hashtag}\n"
        f"Минимум лайков: {min_likes:,}\n\n"
        f"Бот будет проверять каждые "
        f"{config[chat_id].get('interval_minutes', 60)} мин."
    )
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.")
    return ConversationHandler.END


# ─── /remove — удалить хэштег ────────────────────────────────────

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    config = load_config()

    if chat_id not in config or not config[chat_id].get("hashtags"):
        await update.message.reply_text("У вас нет отслеживаемых хэштегов.")
        return

    buttons = []
    for tag in config[chat_id]["hashtags"]:
        buttons.append([InlineKeyboardButton(
            f"❌ #{tag}", callback_data=f"remove_{tag}"
        )])
    buttons.append([InlineKeyboardButton("Отмена", callback_data="remove_cancel")])

    await update.message.reply_text(
        "Какой хэштег удалить?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "remove_cancel":
        await query.edit_message_text("Отменено.")
        return

    tag = query.data.replace("remove_", "")
    chat_id = str(update.effective_chat.id)
    config = load_config()

    if chat_id in config and tag in config[chat_id].get("hashtags", {}):
        del config[chat_id]["hashtags"][tag]
        save_config(config)
        await query.edit_message_text(f"✅ Хэштег #{tag} удалён.")
    else:
        await query.edit_message_text("Хэштег не найден.")


# ─── /list — список хэштегов ─────────────────────────────────────

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    config = load_config()

    if chat_id not in config or not config[chat_id].get("hashtags"):
        await update.message.reply_text("Список пуст. Добавьте хэштег командой /add")
        return

    lines = ["📋 Ваши хэштеги:\n"]
    for tag, data in config[chat_id]["hashtags"].items():
        lines.append(f"  #{tag} — от {data['min_likes']:,} лайков")

    interval = config[chat_id].get("interval_minutes", 60)
    lines.append(f"\n⏰ Интервал проверки: {interval} мин.")
    await update.message.reply_text("\n".join(lines))


# ─── /interval — изменить интервал ───────────────────────────────

async def interval_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    config = load_config()

    if not context.args:
        current = 60
        if chat_id in config:
            current = config[chat_id].get("interval_minutes", 60)
        await update.message.reply_text(
            f"Текущий интервал: {current} мин.\n"
            f"Использование: /interval 30"
        )
        return

    try:
        minutes = int(context.args[0])
        if minutes < 5:
            await update.message.reply_text("Минимум 5 минут.")
            return
    except ValueError:
        await update.message.reply_text("Введите число. Пример: /interval 30")
        return

    if chat_id not in config:
        config[chat_id] = {"hashtags": {}, "interval_minutes": minutes}
    else:
        config[chat_id]["interval_minutes"] = minutes
    save_config(config)

    await update.message.reply_text(f"✅ Интервал изменён на {minutes} мин.")


# ─── /search — ручной поиск ──────────────────────────────────────

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    config = load_config()

    if chat_id not in config or not config[chat_id].get("hashtags"):
        await update.message.reply_text("Сначала добавьте хэштег: /add")
        return

    await update.message.reply_text("🔍 Ищу видео...")

    total_found = 0
    for tag, data in config[chat_id]["hashtags"].items():
        videos = await search_tiktok_by_hashtag(tag, data["min_likes"])
        for video in videos[:5]:
            text = (
                f"🎬 #{tag}\n"
                f"❤️ {video['likes']:,} лайков\n"
                f"💬 {video.get('comments', 0):,} комментов\n"
                f"🔗 {video['url']}"
            )
            await update.message.reply_text(text)
            total_found += 1

    if total_found == 0:
        await update.message.reply_text("Ничего не найдено по вашим критериям.")


# ─── Автоматическая проверка ─────────────────────────────────────

async def auto_check(context: ContextTypes.DEFAULT_TYPE):
    config = load_config()

    for chat_id, chat_data in config.items():
        if not chat_data.get("hashtags"):
            continue

        for tag, data in chat_data["hashtags"].items():
            try:
                videos = await search_tiktok_by_hashtag(tag, data["min_likes"])
                sent_ids = set(data.get("last_sent_ids", []))

                new_videos = [v for v in videos if v["id"] not in sent_ids]

                for video in new_videos[:5]:
                    text = (
                        f"🎬 #{tag}\n"
                        f"❤️ {video['likes']:,} лайков\n"
                        f"💬 {video.get('comments', 0):,} комментов\n"
                        f"🔗 {video['url']}"
                    )
                    await context.bot.send_message(chat_id=int(chat_id), text=text)
                    sent_ids.add(video["id"])

                data["last_sent_ids"] = list(sent_ids)[-100:]
                save_config(config)

            except Exception as e:
                logger.error(f"Ошибка при проверке #{tag}: {e}")


# ─── Main ─────────────────────────────────────────────────────────

def main():
    # Токен берётся из переменной окружения (настраивается в Railway)
    BOT_TOKEN = "8657918741:AAExPdrdrxQ9nnt0qGUen9xBRzBMySCAf_0"

    if not BOT_TOKEN:
        print("❌ Переменная BOT_TOKEN не установлена!")
        print("   Добавьте её в Railway: Variables → BOT_TOKEN → ваш токен")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            WAITING_HASHTAG: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_hashtag)],
            WAITING_LIKES: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_likes)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CallbackQueryHandler(remove_callback, pattern=r"^remove_"))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("interval", interval_cmd))

    app.job_queue.run_repeating(auto_check, interval=60, first=10)

    print("✅ Бот запущен на Railway!")
    app.run_polling()


if __name__ == "__main__":
    main()
