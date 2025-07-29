import os
import requests
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, Bot, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
import telegram.error

# دریافت توکن‌ها و کانال‌ها از محیط
BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_API_KEYS = os.getenv("CMC_API_KEYS")  # کلیدهای API به صورت رشته جدا شده با کاما
REPORT_CHANNEL = os.getenv("REPORT_CHANNEL")  # کانال برای گزارش مصرف API
INFO_CHANNEL = os.getenv("INFO_CHANNEL")    # کانال برای اطلاعات کاربران

# بررسی وجود توکن‌ها
if not BOT_TOKEN:
    print("Error: BOT_TOKEN is not set in environment variables.")
    raise ValueError("BOT_TOKEN در متغیرهای محیطی تنظیم نشده است.")
if not CMC_API_KEYS:
    print("Error: CMC_API_KEYS is not set in environment variables.")
    raise ValueError("CMC_API_KEYS در متغیرهای محیطی تنظیم نشده است.")
if not REPORT_CHANNEL:
    print("Warning: REPORT_CHANNEL is not set. API usage reports will not be sent.")
if not INFO_CHANNEL:
    print("Warning: INFO_CHANNEL is not set. User start reports will not be sent.")

# مدیریت کلیدهای API
api_keys = CMC_API_KEYS.split(",")  # تبدیل رشته کلیدها به لیست
current_key_index = 0
current_api_key = api_keys[current_key_index].strip()

# متغیر برای شماره‌گذاری کاربران
user_counter = 0
user_ids = {}  # دیکشنری برای ذخیره IDهای تخصیص‌یافته به کاربران

# تبدیل امن اعداد
def safe_number(value, fmt="{:,.2f}"):
    return fmt.format(value) if value is not None else "نامشخص"

# بررسی و انتخاب کلید API با کردیت باقی‌مانده
async def check_and_select_api_key(bot: Bot):
    global current_api_key, current_key_index
    url = "https://pro-api.coinmarketcap.com/v1/key/info"
    
    for index, key in enumerate(api_keys):
        key = key.strip()
        headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": key}
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            usage = data.get("data", {}).get("usage", {}).get("current_month", {})
            plan = data.get("data", {}).get("plan", {})
            credits_used = usage.get("credits_used", 0)
            credits_total = plan.get("credit_limit", 10000)  # پیش‌فرض برای پلن رایگان
            credits_left = credits_total - credits_used

            if credits_left > 0:
                current_api_key = key
                current_key_index = index
                print(f"Selected API key: {current_api_key[-6:]} (Key {current_key_index + 1}) with {credits_left} credits left")
                # ارسال پیام به REPORT_CHANNEL
                if REPORT_CHANNEL:
                    try:
                        msg = f"""✅ <b>کلید API انتخاب شد</b>:\n
🔑 کلید شماره: {current_key_index + 1}\n
🟢 کردیت‌های باقی‌مانده: {credits_left:,}\n
🕒 زمان انتخاب: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""
                        await bot.send_message(chat_id=REPORT_CHANNEL, text=msg, parse_mode="HTML")
                        print("✅ پیام انتخاب کلید API به کانال ارسال شد.")
                    except telegram.error.TelegramError as e:
                        print(f"Error sending API key selection message to REPORT_CHANNEL: {e}")
                return True
        except Exception as e:
            print(f"Error checking API key {key[-6:]}: {e}")
            continue
    print("⚠️ هیچ کلید API با کردیت باقی‌مانده پیدا نشد.")
    return False

# تنظیم منوی دستورات
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand("start", "شروع ربات")
    ]
    await bot.set_my_commands(commands)
    print("Bot commands set: /start")

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global user_counter
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name or "بدون نام"

    # تخصیص ID یکتا به کاربر
    if user_id not in user_ids:
        user_counter += 1
        user_ids[user_id] = user_counter
    custom_id = user_ids[user_id]

    # ارسال اطلاعات کاربر به کانال INFO_CHANNEL
    if INFO_CHANNEL:
        try:
            msg = f"""🔔 <b>کاربر جدید ربات را استارت کرد</b>:\n
🆔 ID اختصاصی: {custom_id}\n
🆔 ID تلگرام: {user_id}\n
👤 نام کاربری: {username}\n
🕒 زمان استارت: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""
            await context.bot.send_message(chat_id=INFO_CHANNEL, text=msg, parse_mode="HTML")
            print(f"User start report sent to INFO_CHANNEL for user {user_id} (Custom ID: {custom_id}, Username: {username})")
        except telegram.error.TelegramError as e:
            print(f"Error sending user start report to INFO_CHANNEL: {e}")

    keyboard = [["📊 وضعیت کلی بازار"]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "سلام! 👋\nنام یا نماد یک ارز دیجیتال رو بفرست یا از منوی زیر استفاده کن:",
        parse_mode="HTML",
        reply_markup=markup
    )

# اطلاعات کلی بازار
async def show_global_market(update: Update):
    global current_api_key
    url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}

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
    global current_api_key
    query = update.message.text.strip().lower()

    if query == "📊 وضعیت کلی بازار":
        await show_global_market(update)
        return

    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
    params = {"symbol": query.upper(), "convert": "USD"}

    try:
        print(f"Sending request to CoinMarketCap API for coin: {query}")
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        if "data" not in data or query.upper() not in data["data"]:
            print(f"Error: No data found for {query}")
            await update.message.reply_text("❌ ارز مورد نظر پیدا نشد. لطفاً نام یا نماد دقیق وارد کنید.")
            return

        result = data["data"][query.upper()]
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
🔄 <b>عرضه در گردش</b>: ${safe_number(circulating_supply, "{:,.0f}")} {symbol}\n
🌐 <b>عرضه کل</b>: ${safe_number(total_supply, "{:,.0f}")} {symbol}\n
🚀 <b>عرضه نهایی</b>: ${safe_number(max_supply, "{:,.0f}")} {symbol}\n
🛒 <b>تعداد بازارها</b>: {num_pairs}\n
🏅 <b>رتبه بازار</b>: #{rank}
"""
        keyboard = [[InlineKeyboardButton("📜 نمایش اطلاعات تکمیلی", callback_data=f"details_{symbol}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        print(f"Sending coin info for {symbol} with inline button...")
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)

    except (requests.RequestException, ValueError) as e:
        print(f"Error fetching coin data: {e}")
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات ارز.")

# پردازش کلیک روی دکمه Inline برای اطلاعات تکمیلی
async def handle_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_api_key
    query = update.callback_query
    await query.answer()

    callback_data = query.data
    if callback_data.startswith("details_"):
        symbol = callback_data[len("details_"):]

        # درخواست به API برای اطلاعات تکمیلی
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/info"
        headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
        params = {"symbol": symbol}

        try:
            print(f"Sending request to CoinMarketCap API for details: {symbol}")
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

            if "data" not in data or symbol.upper() not in data["data"]:
                print(f"Error: No details found for {symbol}")
                await query.message.reply_text(f"❌ اطلاعات تکمیلی برای {symbol} پیدا نشد.")
                return

            coin_data = data["data"][symbol.upper()]
            description = coin_data["description"][:500] + "..." if coin_data["description"] else "ناموجود"
            whitepaper = coin_data["urls"].get("technical_doc", ["ناموجود"])[0]
            website = coin_data["urls"].get("website", ["ناموجود"])[0]
            logo = coin_data["logo"] if coin_data["logo"] else "ناموجود"

            # پیام دیالوگ‌مانند
            msg = f"""📜 <b>اطلاعات تکمیلی ارز {coin_data['name']}</b>\n\n
💬 <b>درباره {coin_data['name']}:</b> {description}\n
📄 <b>وایت‌پیپر:</b> {whitepaper}\n
🌐 <b>وب‌سایت:</b> {website}\n
🖼 <b>لوگو:</b> {logo}\n\n
برای بستن این پنجره، روی دکمه زیر کلیک کنید.
"""
            keyboard = [[InlineKeyboardButton("❌ بستن", callback_data=f"close_details_{symbol}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            print(f"Sending detailed info for {symbol}...")
            await query.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)
        except (requests.RequestException, ValueError) as e:
            print(f"Error fetching details for {symbol}: {e}")
            await query.message.reply_text(f"⚠️ خطا در دریافت اطلاعات تکمیلی: {str(e)}\nمطمئن شوید VPN فعال است.")
    else:
        await query.message.reply_text("⚠️ خطا: درخواست نامعتبر.")

# پردازش دکمه "بستن"
async def handle_close_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    print("Closing dialog message...")
    await query.message.delete()

# ارسال گزارش مصرف API (هر 2 دقیقه)
async def send_usage_report_to_channel(bot: Bot):
    global current_api_key, current_key_index
    if not REPORT_CHANNEL:
        print("REPORT_CHANNEL not set.")
        return

    url = "https://pro-api.coinmarketcap.com/v1/key/info"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        print("API response for /v1/key/info:", data)  # چاپ پاسخ خام برای عیب‌یابی

        usage = data.get("data", {}).get("usage", {}).get("current_month", {})
        plan = data.get("data", {}).get("plan", {})

        credits_used = usage.get("credits_used", 0)
        credits_total = plan.get("credit_limit", 10000)  # پیش‌فرض برای پلن رایگان
        plan_name = plan.get("name", "Free")  # پیش‌فرض برای پلن رایگان
        credits_left = credits_total - credits_used

        # ارسال گزارش مصرف API
        msg = f"""📊 <b>وضعیت مصرف API کوین‌مارکت‌کپ</b>:\n
🔹 پلن: {plan_name}\n
🔸 اعتبارات ماهانه: {credits_total:,}\n
✅ مصرف‌شده: {credits_used:,}\n
🟢 باقی‌مانده: {credits_left:,}\n
🔑 کلید API فعال: شماره {current_key_index + 1} ({current_api_key[-6:]})\n
🕒 آخرین بروزرسانی: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""
        await bot.send_message(chat_id=REPORT_CHANNEL, text=msg, parse_mode="HTML")
        print("✅ گزارش مصرف API با موفقیت به کانال ارسال شد.")

        # بررسی محدودیت کردیت و سوییچ به کلید بعدی
        if credits_left <= 0 and current_key_index < len(api_keys) - 1:
            current_key_index += 1
            current_api_key = api_keys[current_key_index].strip()
            print(f"Switched to new API key: {current_api_key[-6:]} (Key {current_key_index + 1})")
            # ارسال پیام هشدار به کانال
            try:
                warning_msg = f"""⚠️ <b>هشدار: کلید API قبلی تمام شد!</b>\n
🔑 به کلید جدید سوییچ شد: شماره {current_key_index + 1} ({current_api_key[-6:]})\n
🕒 زمان سوییچ: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""
                await bot.send_message(chat_id=REPORT_CHANNEL, text=warning_msg, parse_mode="HTML")
                print("✅ پیام هشدار سوییچ کلید به کانال ارسال شد.")
            except telegram.error.TelegramError as e:
                print(f"Error sending API key switch warning to REPORT_CHANNEL: {e}")

    except Exception as e:
        print(f"⚠️ خطا در ارسال گزارش API: {e}")

# ارسال گزارش کلی API (هر 5 دقیقه)
async def send_api_summary_report(bot: Bot):
    if not REPORT_CHANNEL:
        print("REPORT_CHANNEL not set.")
        return

    url = "https://pro-api.coinmarketcap.com/v1/key/info"
    total_credits_used = 0
    total_credits_left = 0
    active_keys = 0

    for key in api_keys:
        key = key.strip()
        headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": key}
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            usage = data.get("data", {}).get("usage", {}).get("current_month", {})
            plan = data.get("data", {}).get("plan", {})
            credits_used = usage.get("credits_used", 0)
            credits_total = plan.get("credit_limit", 10000)  # پیش‌فرض برای پلن رایگان
            credits_left = credits_total - credits_used

            total_credits_used += credits_used
            total_credits_left += credits_left
            if credits_left > 0:
                active_keys += 1
        except Exception as e:
            print(f"Error checking API key {key[-6:]} for summary: {e}")
            continue

    msg = f"""📋 <b>گزارش کلی API کوین‌مارکت‌کپ</b>:\n
🔢 تعداد کل کلیدهای API: {len(api_keys)}\n
🔑 تعداد کلیدهای فعال (با کردیت): {active_keys}\n
✅ کل کردیت‌های مصرف‌شده: {total_credits_used:,}\n
🟢 کل کردیت‌های باقی‌مانده: {total_credits_left:,}\n
🕒 آخرین بروزرسانی: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""
    try:
        await bot.send_message(chat_id=REPORT_CHANNEL, text=msg, parse_mode="HTML")
        print("✅ گزارش کلی API با موفقیت به کانال ارسال شد.")
    except telegram.error.TelegramError as e:
        print(f"Error sending API summary report to REPORT_CHANNEL: {e}")

# تابع اصلی برای اجرای ربات
async def main():
    try:
        print("Initializing Telegram bot...")
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, crypto_info))
        app.add_handler(CallbackQueryHandler(handle_details, pattern="^details_"))
        app.add_handler(CallbackQueryHandler(handle_close_details, pattern="^close_details_"))
        app.add_handler(CommandHandler("setcommands", set_bot_commands))

        # تنظیم منوی دستورات
        await set_bot_commands(app.bot)

        # بررسی و انتخاب کلید API با کردیت هنگام استارت
        print("Checking API keys for available credits...")
        await check_and_select_api_key(app.bot)

        print("Bot is running...")
        await app.initialize()
        await app.start()

        # تلاش برای Polling با مدیریت خطای Conflict
        retry_count = 0
        max_retries = 3
        while retry_count < max_retries:
            try:
                await app.updater.start_polling()
                break  # اگر Polling با موفقیت شروع شد، از حلقه خارج شو
            except telegram.error.Conflict as e:
                retry_count += 1
                print(f"Conflict error occurred. Retry {retry_count}/{max_retries}...")
                await asyncio.sleep(5)  # 5 ثانیه صبر قبل از تلاش مجدد
                if retry_count == max_retries:
                    print("Max retries reached. Stopping bot.")
                    raise e

        # زمان‌بندی ارسال گزارش‌ها
        scheduler = AsyncIOScheduler()
        scheduler.add_job(send_usage_report_to_channel, "interval", minutes=2, args=[app.bot])
        scheduler.add_job(send_api_summary_report, "interval", minutes=5, args=[app.bot])
        scheduler.start()
        print("📅 ارسال گزارش API هر ۲ دقیقه و گزارش کلی هر ۵ دقیقه فعال شد.")
        await asyncio.Event().wait()  # نگه داشتن ربات تا خاموش شدن دستی
    except Exception as e:
        print(f"Error starting bot: {e}")
        raise
    finally:
        await app.stop()
        await app.shutdown()

# اجرای ربات
if __name__ == "__main__":
    asyncio.run(main())
