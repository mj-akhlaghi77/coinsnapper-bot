import requests
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError

# Telegram bot token
BOT_TOKEN = "8195612931:AAEdfLBuIAAx0GKX3gFBHWSjRLGsc78sgUI"  # Replace with your bot token

# CoinMarketCap API key
CMC_API_KEY = "3052d5d6-06fe-4c14-8d0f-444bb09bf638"

# Telegram channel ID (e.g., @YourChannel or -100123456789 for private channels)
CHANNEL_ID = "@coinsnaper"  # Replace with your channel ID

# /start command handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! 👋\nنام یا نماد یک ارز دیجیتال (مثل BTC) رو بنویس تا اطلاعاتش رو برات بفرستم.")

# Function to check if the bot is an admin in the channel
async def check_admin_status(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    try:
        admins = await context.bot.get_chat_administrators(CHANNEL_ID)
        bot_id = context.bot.id
        is_admin = any(admin.user.id == bot_id for admin in admins)
        return is_admin
    except TelegramError as e:
        print(f"Error checking admin status: {e}")
        return False

# Function to forward posts with the relevant hashtag from the channel
async def forward_related_posts(context: ContextTypes.DEFAULT_TYPE, chat_id: int, symbol: str):
    try:
        # Check if bot is admin
        if not await check_admin_status(context, chat_id):
            await context.bot.send_message(chat_id=chat_id, text="⚠️ ربات ادمین کانال نیست یا دسترسی کافی نداره. لطفاً ربات رو به‌عنوان ادمین با تمام مجوزها اضافه کن.")
            return

        # Get last 50 messages from the channel
        messages = []
        async for message in context.bot.get_chat_history(chat_id=CHANNEL_ID, limit=50):
            if message.text and f"#{symbol.lower()}" in message.text.lower():
                messages.append(message.message_id)

        if not messages:
            await context.bot.send_message(chat_id=chat_id, text=f"هیچ پستی با هشتگ #{symbol} در 50 پیام آخر کانال پیدا نشد.")
            return

        # Forward related messages in order (newest to oldest)
        for message_id in messages[:5]:  # Limit to 5 messages to avoid spam
            await context.bot.forward_message(
                chat_id=chat_id,
                from_chat_id=CHANNEL_ID,
                message_id=message_id
            )
            await asyncio.sleep(1)  # 1-second delay to avoid rate limiting
        await context.bot.send_message(chat_id=chat_id, text=f"{len(messages)} پست با هشتگ #{symbol} فوروارد شد.")
    except TelegramError as e:
        print(f"Error forwarding posts: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطا در فوروارد کردن پست‌ها: {e}")

# Handler for user messages to get crypto info and forward posts
async def crypto_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip().lower()

    # Fetch data from CoinMarketCap
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

            # Forward posts with hashtag from channel
            await update.message.reply_text(f"📢 در حال بررسی پست‌های با هشتگ #{symbol} در کانال...")
            await forward_related_posts(context, update.message.chat_id, symbol)
        else:
            await update.message.reply_text("❌ ارز مورد نظر پیدا نشد. لطفاً نام یا نماد دقیق (مثل BTC) رو وارد کن.")

    except requests.RequestException as e:
        print(f"Error fetching CoinMarketCap data: {e}")
        await update.message.reply_text(f"⚠️ خطا در دریافت اطلاعات ارز: {e}")

# Run the bot application
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, crypto_info))

    print("Bot is running...")
    app.run_polling()
