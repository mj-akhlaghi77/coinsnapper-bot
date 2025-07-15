import os
import json
import requests
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, Bot, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# دریافت توکن‌ها و ID ادمین از محیط
BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_API_KEY = os.getenv("CMC_API_KEY")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))  # باید تو Runflare تنظیم بشه

# بررسی وجود توکن‌ها
if not BOT_TOKEN:
    print("Error: BOT_TOKEN is not set in environment variables.")
    raise ValueError("BOT_TOKEN در متغیرهای محیطی تنظیم نشده است.")
if not CMC_API_KEY:
    print("Error: CMC_API_KEY is not set in environment variables.")
    raise ValueError("CMC_API_KEY در متغیرهای محیطی تنظیم نشده است.")
if not ADMIN_USER_ID:
    print("Warning: ADMIN_USER_ID is not set. User list access will be disabled.")

# مسیر فایل ذخیره‌سازی کاربران
USERS_FILE = "users.json"

# تبدیل امن اعداد
def safe_number(value, fmt="{:,.2f}"):
    return fmt.format(value) if value is not None else "نامشخص"

# ذخیره کاربر در فایل JSON
def save_user(user_id, username):
    try:
        # خواندن کاربران فعلی
        try:
            with open(USERS_FILE, "r") as f:
                users = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            users = {}

        # افزودن یا به‌روزرسانی کاربر
        users[str(user_id)] = {
            "username": username or "نامشخص",
            "last_start": datetime.now().isoformat()
        }

        # ذخیره در فایل
        with open(USERS_FILE, "w") as f:
            json.dump(users, f, ensure_ascii=False, indent=4)
        print(f"User {user_id} ({username}) saved to {USERS_FILE}")
    except Exception as e:
        print(f"Error saving user {user_id}: {e}")

# دریافت لیست کاربران
def get_user_list():
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
        return users
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

# تنظیم منوی دستورات
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand("start", "شروع ربات"),
        BotCommand("userlist", "نمایش لیست کاربران (فقط ادمین)")
    ]
    await bot.set_my_commands(commands)
    print("Bot commands set: /start, /userlist")

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name or "بدون نام"

    # ذخیره کاربر
    save_user(user_id, username)

    keyboard = [["📊 وضعیت کلی بازار"]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "سلام! 👋\nنام یا نماد یک ارز دیجیتال رو بفرست یا از منوی زیر استفاده کن:",
        parse_mode="HTML",
        reply_markup=markup
    )

# /userlist (فقط برای ادمین)
async def user_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("⚠️ دسترسی غیرمجاز! این دستور فقط برای ادمین است.")
        print(f"Unauthorized access attempt to /userlist by user {user_id}")
        return

    users = get_user_list()
    if not users:
        await update.message.reply_text("هیچ کاربری ربات را استارت نکرده است.")
        print("No users found in user list")
        return

    total_users = len(users)
    msg = f"<b>تعداد کل کاربران</b>: {total_users}\n\n<b>لیست کاربران</b>:\n"
    for uid, info in users.items():
        msg += f"ID: {uid}, نام کاربری: {info['username']}, آخرین استارت: {info['last_start']}\n"

    await update.message.reply_text(msg, parse_mode="HTML")
    print(f"User list sent to admin {user_id}")

# اطلاعات کلی بازار
async def show_global_market(update: Update):
    url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": CMC_API_KEY}

    try:
        print("Sending request to CoinMarketCap API for global market data...")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if "data" not in data:
            print("Error: 'data' key not found in API response.")
            raise ValueError("پاسخ API شامل کلید 'data' نیست.")

        total_market_cap = data["data"]["quote"]["USD"]["total_market_cap"]
        total_volume_24h = data["data"]["quote"]["USD"]["total_volume_24h"]
        btc_dominance = data["data"]["btc_dominance"]

        msg = f"""🌐 <b>وضعیت کلی بازار کریپتو</b>:\n
💰 <b>ارزش کل بازار</b>: ${safe_number(total_market_cap, "{:,.0f}")}\n
📊 <b>حجم معاملات ۲۴ساعته</b>: ${safe_number(total_volume_24h, "{:,.0f}")}\n
🟠 <b>دامیننس بیت‌کوین</b>: {safe_number(btc_dominance, "{:.2f}")}%
"""
        await update.message.reply_text(msg, parse_mode="HTML")

    except (requests.RequestException, ValueError) as e:
        print(f"Global market error: {e}")
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات کلی بازار.")

# هندل پیام‌ها
async def crypto_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip().lower()

    if query == "📊 وضعیت کلی بازار":
        await show_global_market(update)
        return

    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": CMC_API_KEY}
    params = {"start": 1, "limit": 5000, "convert": "USD"}

    try:
        print(f"Sending request to CoinMarketCap API for coin: {query}")
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        if "data" not in data:
            print("Error: 'data' key not found in API response.")
            raise ValueError("پاسخ API شامل کلید 'data' نیست.")

        result = None
        for coin in data["data"]:
            if coin["name"].lower() == query or coin["symbol"].lower() == query:
                result = coin
                break

        if result:
            name = result["name"]
            symbol = result["symbol"]
            price = result["quote"]["USD"]["price"]
            change_1h = result["quote"]["USD"]["percent_change_1h"]
            change_24h = result["quote"]["USD"]["percent_change_24h"]
            change_7d = result["quote"]["USD"]["percent_change_7d"]
            market_cap = result["quote"]["USD"]["market_cap"]
            volume_24h = result["quote"]["USD"]["volume_24h"]
            circulating_supply = result["circulating_supply"]
            total_supply = result["total_supply"]
            max_supply = result["max_supply"]
            num_pairs = result["num_market_pairs"]
            rank = result["cmc_rank"]

            msg = f"""🔍 <b>اطلاعات ارز</b>:\n
🏷️ <b>نام</b>: {name}\n
💱 <b>نماد</b>: {symbol}\n
💵 <b>قیمت</b>: ${safe_number(price)}\n
⏱️ <b>تغییر ۱ ساعته</b>: {safe_number(change_1h, "{:.2f}")}%\n
📊 <b>تغییر ۲۴ ساعته</b>: {safe_number(change_24h, "{:.2f}")}%\n
📅 <b>تغییر ۷ روزه</b>: {safe_number(change_7d, "{:.2f}")}%\n
📈 <b>حجم معاملات ۲۴ساعته</b>: ${safe_number(volume_24h, "{:,.0f}")}\n
💰 <b>ارزش کل بازار</b>: ${safe_number(market_cap, "{:,.0f}")}\n
🔄 <b>عرضه در گردش</b>: {safe_number(circulating_supply, "{:,.0f}")} {symbol}\n
🌐 <b>عرضه کل</b>: {safe_number(total_supply, "{:,.0f}")} {symbol}\n
🚀 <b>عرضه نهایی</b>: {safe_number(max_supply, "{:,.0f}")} {symbol}\n
🛒 <b>تعداد بازارها</b>: {num_pairs}\n
🏅 <b>رتبه بازار</b>: #{rank}
"""
            keyboard = [[InlineKeyboardButton("📜 نمایش اطلاعات تکمیلی", callback_data=f"details_{symbol}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            print(f"Sending coin info for {symbol} with inline button...")
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await update.message.reply_text("❌ ارز مورد نظر پیدا نشد. لطفاً نام یا نماد دقیق وارد کنید.")

    except (requests.RequestException, ValueError) as e:
        print(f"Error fetching coin data: {e}")
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات ارز.")

# پردازش کلیک روی دکمه Inline
async def handle_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    callback_data = query.data
    if callback_data.startswith("details_"):
        symbol = callback_data[len("details_"):]
        msg = f"""📜 <b>اطلاعات تکمیلی ارز {symbol}</b>\n\n
اینجا اطلاعات تکمیلی ارز {symbol} نمایش داده می‌شود.\n
برای بستن این پنجره، روی دکمه زیر کلیک کنید.
"""
        keyboard = [[InlineKeyboardButton("❌ بستن", callback_data=f"close_details_{symbol}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        print(f"Sending dialog-like message for {symbol}...")
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await query.message.reply_text("⚠️ خطا: درخواست نامعتبر.")

# پردازش دکمه "بستن"
async def handle_close_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    print("Closing dialog message...")
    await query.message.delete()

# اجرای ربات
if __name__ == "__main__":
    try:
        print("Initializing Telegram bot...")
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("userlist", user_list))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, crypto_info))
        app.add_handler(CallbackQueryHandler(handle_details, pattern="^details_"))
        app.add_handler(CallbackQueryHandler(handle_close_details, pattern="^close_details_"))

        # تنظیم منوی دستورات به‌صورت مستقیم
        bot = Bot(token=BOT_TOKEN)
        import asyncio
        asyncio.run(set_bot_commands(bot))

        print("Bot is running...")
        app.run_polling()
    except Exception as e:
        print(f"Error starting bot: {e}")
        raise
