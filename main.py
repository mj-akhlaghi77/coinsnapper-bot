import os
import json
import requests
import asyncio
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, Bot, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_API_KEY = os.getenv("CMC_API_KEY")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN در متغیرهای محیطی تنظیم نشده است.")
if not CMC_API_KEY:
    raise ValueError("CMC_API_KEY در متغیرهای محیطی تنظیم نشده است.")

USERS_FILE = "users.json"

def safe_number(value, fmt="{:,.2f}"):
    return fmt.format(value) if value is not None else "نامشخص"

def save_user(user_id, username):
    try:
        try:
            with open(USERS_FILE, "r") as f:
                users = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            users = {}

        users[str(user_id)] = {
            "username": username or "نامشخص",
            "last_start": datetime.now().isoformat()
        }

        with open(USERS_FILE, "w") as f:
            json.dump(users, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Error saving user {user_id}: {e}")

def get_user_list():
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand("start", "شروع ربات"),
        BotCommand("settings", "تنظیمات ادمین (فقط ادمین)")
    ]
    await bot.set_my_commands(commands)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username or user.first_name or "بدون نام")
    keyboard = [["\U0001F4CA وضعیت کلی بازار"]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "سلام! \U0001F44B\nنام یا نماد یک ارز دیجیتال رو بفرست یا از منوی زیر استفاده کن:",
        parse_mode="HTML",
        reply_markup=markup
    )

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("⚠️ دسترسی غیرمجاز! این دستور فقط برای ادمین است.")
        return

    users = get_user_list()
    if not users:
        await update.message.reply_text("هیچ کاربری ربات را استارت نکرده است.")
        return

    msg = f"<b>تنظیمات ادمین</b>:\n\n"
    for uid, info in users.items():
        msg += f"ID: {uid}, نام کاربری: {info['username']}, آخرین استارت: {info['last_start']}\n"

    await update.message.reply_text(msg, parse_mode="HTML")

async def show_global_market(update: Update):
    url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": CMC_API_KEY}

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()["data"]

        msg = f"""\U0001F310 <b>وضعیت کلی بازار کریپتو</b>:\n
💰 <b>ارزش کل بازار</b>: ${safe_number(data['quote']['USD']['total_market_cap'], "{:,.0f}")}\n
📊 <b>حجم معاملات ۲۴ساعته</b>: ${safe_number(data['quote']['USD']['total_volume_24h'], "{:,.0f}")}\n
🟠 <b>دامیننس بیت‌کوین</b>: {safe_number(data['btc_dominance'], "{:.2f}")}%"""
        await update.message.reply_text(msg, parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات کلی بازار.")

async def crypto_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip().lower()

    if query == "📊 وضعیت کلی بازار":
        await show_global_market(update)
        return

    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": CMC_API_KEY}
    params = {"start": 1, "limit": 5000, "convert": "USD"}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        coins = response.json()["data"]

        coin = next((c for c in coins if c["name"].lower() == query or c["symbol"].lower() == query), None)
        if not coin:
            await update.message.reply_text("❌ ارز مورد نظر پیدا نشد. لطفاً نام یا نماد دقیق وارد کنید.")
            return

        msg = f"""🔍 <b>اطلاعات ارز</b>:

🏷️ <b>نام</b>: {coin['name']}\n
💱 <b>نماد</b>: {coin['symbol']}\n
💵 <b>قیمت</b>: ${safe_number(coin['quote']['USD']['price'])}\n
⏱️ <b>تغییر ۱ ساعته</b>: {safe_number(coin['quote']['USD']['percent_change_1h'], "{:.2f}")}%\n
📊 <b>تغییر ۲۴ ساعته</b>: {safe_number(coin['quote']['USD']['percent_change_24h'], "{:.2f}")}%\n
📅 <b>تغییر ۷ روزه</b>: {safe_number(coin['quote']['USD']['percent_change_7d'], "{:.2f}")}%\n
📈 <b>حجم معاملات ۲۴ساعته</b>: ${safe_number(coin['quote']['USD']['volume_24h'], "{:,.0f}")}\n
💰 <b>ارزش کل بازار</b>: ${safe_number(coin['quote']['USD']['market_cap'], "{:,.0f}")}\n
🔄 <b>عرضه در گردش</b>: {safe_number(coin['circulating_supply'], "{:,.0f}")} {coin['symbol']}\n
🌐 <b>عرضه کل</b>: {safe_number(coin['total_supply'], "{:,.0f}")} {coin['symbol']}\n
🚀 <b>عرضه نهایی</b>: {safe_number(coin['max_supply'], "{:,.0f}")} {coin['symbol']}\n
🛒 <b>تعداد بازارها</b>: {coin['num_market_pairs']}\n
🏅 <b>رتبه بازار</b>: #{coin['cmc_rank']}"""

        keyboard = [[InlineKeyboardButton("📜 نمایش اطلاعات تکمیلی", callback_data=f"details_{coin['symbol']}")]]
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات ارز.")

async def handle_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    symbol = query.data.split("_")[1]
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/info"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": CMC_API_KEY}
    params = {"symbol": symbol}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()["data"].get(symbol)

        if not data:
            await query.message.reply_text("⚠️ اطلاعاتی برای این ارز پیدا نشد.")
            return

        description = data.get("description", "توضیحی موجود نیست.")
        category = data.get("category", "نامشخص")
        website = data.get("urls", {}).get("website", [""])[0]
        explorers = data.get("urls", {}).get("explorer", [])
        explorer_links = "\n".join([f"🔗 {link}" for link in explorers[:3]]) if explorers else "🔍 در دسترس نیست."
        whitepaper = data.get("urls", {}).get("technical_doc", [])
        whitepaper_link = whitepaper[0] if whitepaper else None
        date_added = data.get("date_added", "نامشخص")
        tags = ", ".join(data.get("tags", [])[:5]) or "ندارد"
        platform = data.get("platform", {}).get("name", "ندارد")

        whitepaper_text = f"<a href=\"{whitepaper_link}\">{whitepaper_link}</a>" if whitepaper_link else "موجود نیست"

        msg = f"""📜 <b>اطلاعات تکمیلی درباره {symbol}</b>

📂 <b>دسته‌بندی</b>: {category}
🌐 <b>وب‌سایت رسمی</b>: <a href=\"{website}\">{website}</a>
🧾 <b>توضیحات</b>: {description[:1000]}...
📆 <b>تاریخ اضافه شدن</b>: {date_added}
🏷 <b>برچسب‌ها</b>: {tags}
⚙️ <b>پلتفرم</b>: {platform}
📘 <b>وایت‌پیپر</b>: {whitepaper_text}
🛰 <b>اکسپلوررها</b>:
{explorer_links}"""

        keyboard = [[InlineKeyboardButton("❌ بستن", callback_data=f"close_details_{symbol}")]]
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard), disable_web_page_preview=True)

    except Exception as e:
        print(f"Error fetching details: {e}")
        await query.message.reply_text("⚠️ خطا در دریافت اطلاعات تکمیلی.")

async def handle_close_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, crypto_info))
    app.add_handler(CallbackQueryHandler(handle_details, pattern="^details_"))
    app.add_handler(CallbackQueryHandler(handle_close_details, pattern="^close_details_"))
    app.add_handler(CommandHandler("setcommands", set_bot_commands))

    # Get the current event loop or create a new one if none exists
    loop = asyncio.get_event_loop()
    try:
        if loop.is_running():
            # If loop is already running, create a new task
            await app.initialize()
            await set_bot_commands(app.bot)
            print("Bot is running...")
            await app.run_polling()
            await app.shutdown()
        else:
            # If no loop is running, use run_until_complete
            loop.run_until_complete(app.initialize())
            await set_bot_commands(app.bot)
            print("Bot is running...")
            loop.run_until_complete(app.run_polling())
            loop.run_until_complete(app.shutdown())
    finally:
        if not loop.is_running():
            loop.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "This event loop is already running" in str(e):
            # If asyncio.run fails due to an existing loop, run main directly
            loop = asyncio.get_event_loop()
            loop.create_task(main())
        else:
            raise
