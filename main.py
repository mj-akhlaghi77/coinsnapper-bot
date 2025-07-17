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

# به جای فایل، از یه دیکشنری موقت برای ذخیره کاربران استفاده می‌کنیم
USERS = {}

def safe_number(value, fmt="{:,.2f}"):
    return fmt.format(value) if value is not None else "نامشخص"

def save_user(user_id, username):
    try:
        USERS[str(user_id)] = {
            "username": username or "نامشخص",
            "last_start": datetime.now().isoformat()
        }
        print(f"کاربر {user_id} ذخیره شد.")
    except Exception as e:
        print(f"خطا در ذخیره کاربر {user_id}: {e}")

def get_user_list():
    return USERS

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand("start", "شروع ربات"),
        BotCommand("settings", "تنظیمات ادمین (فقط ادمین)")
    ]
    try:
        await bot.set_my_commands(commands)
        print("دستورات ربات تنظیم شد.")
    except Exception as e:
        print(f"خطا در تنظیم دستورات ربات: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username or user.first_name or "بدون نام")
    keyboard = [["\U0001F4CA وضعیت کلی بازار"]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    try:
        await update.message.reply_text(
            "سلام! \U0001F44B\nنام یا نماد یک ارز دیجیتال رو بفرست یا از منوی زیر استفاده کن:",
            parse_mode="HTML",
            reply_markup=markup
        )
    except Exception as e:
        print(f"خطا در ارسال پیام شروع: {e}")

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

    try:
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        print(f"خطا در ارسال تنظیمات: {e}")

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
        print(f"خطا در دریافت اطلاعات بازار: {e}")
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
        print(f"خطا در دریافت اطلاعات ارز: {e}")
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
        print(f"خطا در دریافت اطلاعات تکمیلی: {e}")
        await query.message.reply_text("⚠️ خطا در دریافت اطلاعات تکمیلی.")

async def handle_close_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
        await query.message.delete()
    except Exception as e:
        print(f"خطا در بستن جزئیات: {e}")

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, crypto_info))
    app.add_handler(CallbackQueryHandler(handle_details, pattern="^details_"))
    app.add_handler(CallbackQueryHandler(handle_close_details, pattern="^close_details_"))
    app.add_handler(CommandHandler("setcommands", set_bot_commands))

    try:
        # Initialize the application
        await app.initialize()
        print("اپلیکیشن اولیه‌سازی شد.")
        # Set bot commands
        await set_bot_commands(app.bot)
        # Start polling
        print("ربات در حال اجرا...")
        await app.run_polling()
    except Exception as e:
        print(f"خطا در اجرای ربات: {e}")
    finally:
        # Ensure proper shutdown
        try:
            await app.shutdown()
            print("اپلیکیشن خاموش شد.")
        except Exception as e:
            print(f"خطا در خاموش کردن اپلیکیشن: {e}")

if __name__ == "__main__":
    try:
        # Try to get the running event loop
        loop = asyncio.get_running_loop()
        # Schedule the main coroutine as a task
        loop.create_task(main())
        print("ربات به صورت task در loop فعلی اجرا شد.")
    except RuntimeError:
        # If no running loop exists, create a new one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(main())
        except Exception as e:
            print(f"خطا در اجرای loop جدید: {e}")
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
                print("Loop بسته شد.")
            except Exception as e:
                print(f"خطا در بستن loop: {e}")
