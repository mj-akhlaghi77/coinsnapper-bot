import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Bot token and CoinMarketCap API key
BOT_TOKEN = "توکن واقعی"
CMC_API_KEY = "کلید واقعی"

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! 👋\nنام یا نماد یک ارز دیجیتال (مثل BTC یا Ethereum) رو بنویس تا اطلاعاتش رو برات بفرستم.")

# Message handler
async def crypto_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip().lower()

    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    headers = {
        "Accepts": "application/json",
        "X-CMC_PRO_API_KEY": CMC_API_KEY,
    }
    params = {"limit": 200, "convert": "USD"}

    try:
        response = requests.get(url, headers=headers, params=params)
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
            change_24h = result["quote"]["USD"]["percent_change_24h"]
            market_cap = result["quote"]["USD"]["market_cap"]
            circulating_supply = result["circulating_supply"]
            total_supply = result["total_supply"]
            rank = result["cmc_rank"]

            msg = f"""🔍 اطلاعات ارز:
🏷️ نام: {name}
💱 نماد: {symbol}
💵 قیمت: ${price:,.2f}
📊 تغییر ۲۴ ساعته: {change_24h:.2f}%
💰 ارزش کل بازار: ${market_cap:,.2f}
🔄 عرضه در گردش: {circulating_supply:,.0f} {symbol}
🌐 عرضه کل: {total_supply:,.0f} {symbol}
🏅 رتبه بازار: #{rank}"""
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text("❌ ارز مورد نظر پیدا نشد. لطفاً نام یا نماد دقیق (مثل BTC) رو وارد کن.")

    except requests.RequestException as e:
        print(f"Error fetching data: {e}")
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات ارز. لطفاً دوباره تلاش کن.")

# Run bot
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, crypto_info))

    print("Bot is running...")
    app.run_polling()
