import os
import requests
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# دریافت توکن‌ها از متغیر محیطی
BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_API_KEY = os.getenv("CMC_API_KEY")

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["📊 وضعیت کلی بازار"]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "سلام! 👋\nنام یا نماد یک ارز دیجیتال (مثل BTC یا Ethereum) رو بفرست یا از منوی زیر استفاده کن:",
        reply_markup=markup
    )

# نمایش وضعیت کلی بازار
async def show_global_market(update: Update):
    global_url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
    headers = {
        "Accepts": "application/json",
        "X-CMC_PRO_API_KEY": CMC_API_KEY,
    }

    try:
        response = requests.get(global_url, headers=headers)
        response.raise_for_status()
        data = response.json()["data"]

        total_market_cap = data["quote"]["USD"]["total_market_cap"]
        total_volume_24h = data["quote"]["USD"]["total_volume_24h"]
        btc_dominance = data["btc_dominance"]

        msg = f"""🌐 وضعیت کلی بازار کریپتو:
💰 ارزش کل بازار: ${total_market_cap:,.0f}
📊 حجم معاملات ۲۴ساعته: ${total_volume_24h:,.0f}
🟠 دامیننس بیت‌کوین: {btc_dominance:.2f}%"""

        await update.message.reply_text(msg)

    except requests.RequestException as e:
        print(f"Global market error: {e}")
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات کلی بازار.")

# پاسخ به ارز دیجیتال یا دکمه کلی بازار
async def crypto_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip().lower()

    if query == "📊 وضعیت کلی بازار":
        await show_global_market(update)
        return

    listings_url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    headers = {
        "Accepts": "application/json",
        "X-CMC_PRO_API_KEY": CMC_API_KEY,
    }
    params = {"limit": 200, "convert": "USD"}

    try:
        response = requests.get(listings_url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        result = next(
            (coin for coin in data["data"]
             if coin["name"].lower() == query or coin["symbol"].lower() == query),
            None
        )

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
💵 قیمت: ${price:,.2f}
⏱️ تغییر ۱ ساعته: {change_1h:.2f}%
📊 تغییر ۲۴ ساعته: {change_24h:.2f}%
📅 تغییر ۷ روزه: {change_7d:.2f}%
📈 حجم معاملات ۲۴ساعته: ${volume_24h:,.0f}
💰 ارزش کل بازار: ${market_cap:,.2f}
🔄 عرضه در گردش: {circulating_supply:,.0f} {symbol}
🌐 عرضه کل: {total_supply:,.0f} {symbol}
🚀 عرضه نهایی: {max_supply:,.0f} {symbol}
🛒 تعداد بازارها: {num_pairs}
🏅 رتبه بازار: #{rank}"""

            await update.message.reply_text(msg)
        else:
            await update.message.reply_text("❌ ارز مورد نظر پیدا نشد. لطفاً نام یا نماد دقیق (مثل BTC) رو وارد کن.")

    except requests.RequestException as e:
        print(f"خطا در دریافت اطلاعات ارز: {e}")
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات ارز. لطفاً بعداً تلاش کن.")

# اجرای ربات
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, crypto_info))

    print("Bot is running...")
    app.run_polling()
