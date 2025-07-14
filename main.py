import os
import requests
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# دریافت توکن‌ها از محیط
BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_API_KEY = os.getenv("CMC_API_KEY")

# بررسی وجود توکن‌ها
if not BOT_TOKEN or not CMC_API_KEY:
    raise ValueError("BOT_TOKEN یا CMC_API_KEY در متغیرهای محیطی تنظیم نشده‌اند.")

# 🔧 تبدیل امن اعداد (برای جلوگیری از ارور NoneType)
def safe_number(value, fmt="{:,.2f}"):
    return fmt.format(value) if value is not None else "نامشخص"

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["📊 وضعیت کلی بازار"]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "سلام! 👋\nنام یا نماد یک ارز دیجیتال رو بفرست یا از منوی زیر استفاده کن:",
        reply_markup=markup
    )

# اطلاعات کلی بازار
async def show_global_market(update: Update):
    url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
    headers = {
        "Accepts": "application/json",
        "X-CMC_PRO_API_KEY": CMC_API_KEY,
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if "data" not in data:
            raise ValueError("پاسخ API شامل کلید 'data' نیست.")

        total_market_cap = data["data"]["quote"]["USD"]["total_market_cap"]
        total_volume_24h = data["data"]["quote"]["USD"]["total_volume_24h"]
        btc_dominance = data["data"]["btc_dominance"]

        msg = f"""🌐 وضعیت کلی بازار کریپتو:
💰 ارزش کل بازار: ${safe_number(total_market_cap, "{:,.0f}")}
📊 حجم معاملات ۲۴ساعته: ${safe_number(total_volume_24h, "{:,.0f}")}
🟠 دامیننس بیت‌کوین: {safe_number(btc_dominance, "{:.2f}")}%"""

        await update.message.reply_text(msg)

    except (requests.RequestException, ValueError) as e:
        print(f"Global market error: {e}")
        await update.message.reply_text("⚠️ خطا در دریافت威力 دریافت اطلاعات کلی بازار.")

# هندل پیام‌ها
async def crypto_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip().lower()

    if query == "📊 وضعیت کلی بازار":
        await show_global_market(update)
        return

    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    headers = {
        "Accepts": "application/json",
        "X-CMC_PRO_API_KEY": CMC_API_KEY,
    }
    params = {"start": 1, "limit": 5000, "convert": "USD"}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        if "data" not in data:
            raise ValueError("پاسخ API شامل کلید 'data' نیست.")

        result = None
        for coin in data["data"]:
            name_match = coin["name"].lower() == query
            symbol_match = coin["symbol"].lower() == query
            if name_match or symbol_match:
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

            msg = f"""🔍 اطلاعات ارز:
🏷️ نام: {name}
💱 نماد: {symbol}
💵 قیمت: ${safe_number(price)}
⏱️ تغییر ۱ ساعته: {safe_number(change_1h, "{:.2f}")}%
📊 تغییر ۲۴ ساعته: {safe_number(change_24h, "{:.2f}")}%
📅 تغییر ۷ روزه: {safe_number(change_7d, "{:.2f}")}%
📈 حجم معاملات ۲۴ساعته: ${safe_number(volume_24h, "{:,.0f}")}
💰 ارزش کل بازار: ${safe_number(market_cap, "{:,.0f}")}
🔄 عرضه در گردش: {safe_number(circulating_supply, "{:,.0f}")} {symbol}
🌐 عرضه کل: {safe_number(total_supply, "{:,.0f}")} {symbol}
🚀 عرضه نهایی: {safe_number(max_supply, "{:,.0f}")} {symbol}
🛒 تعداد بازارها: {num_pairs}
🏅 رتبه بازار: #{rank}"""

            # اضافه کردن دکمه Inline
            keyboard = [[InlineKeyboardButton("📜 نمایش اطلاعات تکمیلی", callback_data=f"details_{symbol}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(msg, reply_markup=reply_markup)
        else:
            await update.message.reply_text("❌ ارز مورد نظر پیدا نشد. لطفاً نام یا نماد دقیق وارد کنید.")

    except (requests.RequestException, ValueError) as e:
        print(f"خطا در دریافت اطلاعات ارز: {e}")
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات ارز.")

# پردازش کلیک روی دکمه Inline
async def handle_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # تأیید دریافت کلیک

    # استخراج نماد ارز از callback_data
    callback_data = query.data
    if callback_data.startswith("details_"):
        symbol = callback_data[len("details_"):]  # استخراج نماد ارز (مثل BTC)
        coin_name = symbol  # برای سادگی، فعلاً از symbol استفاده می‌کنیم

        # ارسال پیام ساده برای نمایش اطلاعات تکمیلی
        await query.message.reply_text(f"📜 اطلاعات تکمیلی ارز {coin_name}")
    else:
        await query.message.reply_text("⚠️ خطا: درخواست نامعتبر.")

# اجرای ربات
if __name__ == "__main__":
    try:
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, crypto_info))
        app.add_handler(CallbackQueryHandler(handle_details, pattern="^details_"))

        print("Bot is running...")
        app.run_polling()
    except Exception as e:
        print(f"Error starting bot: {e}")
        raise
