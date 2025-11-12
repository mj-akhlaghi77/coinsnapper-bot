# main.py
import os
import requests
from datetime import datetime, timedelta, date
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, Bot, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
import telegram.error
import psycopg2
from psycopg2.extras import DictCursor

# -------------------------
# محیط و تنظیمات اولیه
# -------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
REPORT_CHANNEL = os.getenv("REPORT_CHANNEL")  # کانال برای گزارش مصرف API
INFO_CHANNEL = os.getenv("INFO_CHANNEL")    # کانال برای اطلاعات کاربران و پرداخت‌ها
CMC_API_KEY_1 = os.getenv("CMC_API_KEY_1")
CMC_API_KEY_2 = os.getenv("CMC_API_KEY_2")
CMC_API_KEY_3 = os.getenv("CMC_API_KEY_3")
TRON_ADDRESS = os.getenv("TRON_ADDRESS")  # آدرس ترون از متغیر محیطی
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS = os.getenv("ADMIN_IDS")  # رشته‌ای مثل "12345678,87654321" (آی‌دی‌های تلگرام ادمین‌ها)

if BOT_TOKEN is None:
    raise ValueError("BOT_TOKEN تنظیم نشده است.")

if DATABASE_URL is None:
    raise ValueError("DATABASE_URL تنظیم نشده است. در Render متغیر محیطی DATABASE_URL را اضافه کنید.")

if TRON_ADDRESS is None:
    # اگر کاربر آدرس را در متغیر گذاشته، بهتره استفاده بشه، ولی اگر نه ما با پیام واضح جلو می‌ریم.
    print("هشدار: TRON_ADDRESS در متغیرهای محیطی تنظیم نشده است. پیام پرداخت حاوی آدرس نخواهد بود.")

# لیست کلیدهای CMC
api_keys = []
for k in (CMC_API_KEY_1, CMC_API_KEY_2, CMC_API_KEY_3):
    if k:
        api_keys.append(k.strip())

current_key_index = 0
current_api_key = api_keys[current_key_index] if api_keys else None

# تبدیل ADMIN_IDS به لیست عددی
if ADMIN_IDS:
    try:
        ADMIN_ID_LIST = [int(x.strip()) for x in ADMIN_IDS.split(",") if x.strip()]
    except Exception:
        ADMIN_ID_LIST = []
        print("فرمت ADMIN_IDS اشتباه است. باید مانند: 12345678,87654321 باشد.")
else:
    ADMIN_ID_LIST = []

# -------------------------
# کمکی‌های دیتابیس
# -------------------------
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # جدول users: فقط telegram_id، last_free_use (DATE)، subscription_expiry (TIMESTAMP)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            last_free_use DATE,
            subscription_expiry TIMESTAMP,
            registered_at TIMESTAMP DEFAULT NOW()
        );
    """)
    # جدول payments برای ثبت هش‌ها و وضعیت آنها
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            tx_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', -- pending, approved, rejected
            note TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            processed_at TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ دیتابیس و جداول (users, payments) ایجاد شدند (در صورت نبود).")

# -------------------------
# کمکی‌های اشتراک و دسترسی
# -------------------------
def activate_user_subscription(telegram_id: int, days: int = 30):
    """اشتراک کاربر را فعال یا تمدید می‌کند (۳۰ روز به صورت پیش‌فرض)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT subscription_expiry FROM users WHERE telegram_id = %s", (telegram_id,))
    rec = cur.fetchone()
    now = datetime.now()
    if rec and rec["subscription_expiry"] and rec["subscription_expiry"] > now:
        # تمدید از تاریخ فعلی اشتراک
        new_expiry = rec["subscription_expiry"] + timedelta(days=days)
    else:
        new_expiry = now + timedelta(days=days)
    cur.execute("UPDATE users SET subscription_expiry = %s, last_free_use = NULL WHERE telegram_id = %s", (new_expiry, telegram_id))
    conn.commit()
    cur.close()
    conn.close()
    return new_expiry

def check_subscription_status(telegram_id: int):
    """برمی‌گرداند (is_subscribed: bool, days_remaining: int or 0)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT subscription_expiry FROM users WHERE telegram_id = %s", (telegram_id,))
    rec = cur.fetchone()
    cur.close()
    conn.close()
    if not rec or not rec["subscription_expiry"]:
        return False, 0
    expiry = rec["subscription_expiry"]
    now = datetime.now()
    if expiry > now:
        return True, (expiry - now).days
    return False, 0

def register_user_if_not_exists(telegram_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE telegram_id = %s", (telegram_id,))
    rec = cur.fetchone()
    if not rec:
        cur.execute("INSERT INTO users (telegram_id) VALUES (%s)", (telegram_id,))
        conn.commit()
    cur.close()
    conn.close()

def record_free_use(telegram_id: int):
    """ثبت می‌کند که کاربر امروز استفاده‌ی رایگانش را انجام داده."""
    conn = get_db_connection()
    cur = conn.cursor()
    today = date.today()
    cur.execute("UPDATE users SET last_free_use = %s WHERE telegram_id = %s", (today, telegram_id))
    conn.commit()
    cur.close()
    conn.close()

def has_free_use_today(telegram_id: int) -> bool:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT last_free_use FROM users WHERE telegram_id = %s", (telegram_id,))
    rec = cur.fetchone()
    cur.close()
    conn.close()
    if rec and rec["last_free_use"]:
        return rec["last_free_use"] == date.today()
    return False

# -------------------------
# توابع کمکی نمایش و ایمن‌سازی
# -------------------------
def safe_number(value, fmt="{:,.2f}"):
    return fmt.format(value) if value is not None else "نامشخص"

# -------------------------
# مدیریت کلیدهای API (بدون تغییر اساسی)
# -------------------------
async def check_and_select_api_key(bot: Bot):
    global current_api_key, current_key_index
    if not api_keys:
        print("No API keys available.")
        if REPORT_CHANNEL:
            try:
                await bot.send_message(chat_id=REPORT_CHANNEL, text="⚠️ هیچ کلید API (CMC) تنظیم نشده است.", parse_mode="HTML")
            except telegram.error.TelegramError as e:
                print(f"Error sending CMC_API_KEYS error to REPORT_CHANNEL: {e}")
        return False

    url = "https://pro-api.coinmarketcap.com/v1/key/info"
    for index, key in enumerate(api_keys):
        key = key.strip()
        headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": key}
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            usage = data.get("data", {}).get("usage", {}).get("current_month", {})
            plan = data.get("data", {}).get("plan", {})
            credits_used = usage.get("credits_used", 0)
            credits_total = plan.get("credit_limit", 10000)
            credits_left = credits_total - credits_used
            if credits_left > 0:
                current_api_key = key
                current_key_index = index
                if REPORT_CHANNEL:
                    try:
                        msg = f"✅ کلید API انتخاب شد: شماره {current_key_index+1} — باقی‌مانده: {credits_left:,}"
                        await bot.send_message(chat_id=REPORT_CHANNEL, text=msg, parse_mode="HTML")
                    except telegram.error.TelegramError:
                        pass
                return True
        except Exception as e:
            print(f"Error checking API key {index+1}: {e}")
            continue
    if REPORT_CHANNEL:
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL, text="⚠️ هیچ کلید API با کردیت باقی‌مانده پیدا نشد.", parse_mode="HTML")
        except telegram.error.TelegramError:
            pass
    return False

# -------------------------
# هندلرها
# -------------------------
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand("start", "شروع ربات"),
        BotCommand("check", "بررسی وضعیت اشتراک"),
        BotCommand("verify", "ارسال هش تراکنش: /verify <tx_hash>")
    ]
    await bot.set_my_commands(commands)
    print("Bot commands set: /start, /check, /verify")

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # ثبت کاربر اگر وجود نداشته باشد (فقط telegram_id)
    register_user_if_not_exists(user_id)

    # بررسی اشتراک
    subscribed, days_left = check_subscription_status(user_id)

    if subscribed:
        await update.message.reply_text(f"✅ اشتراک شما فعال است.\n⏰ حدوداً {days_left} روز تا پایان اشتراک باقی مانده.")
    else:
        # اگر ثبت‌نام کرده ولی اشتراک ندارد یا منقضی شده
        tron_msg = TRON_ADDRESS if TRON_ADDRESS else "آدرس پرداخت تعریف نشده است. لطفاً با ادمین تماس بگیرید."
        msg = (
            "👋 سلام!\n\n"
            "به ربات خوش آمدی. برای فعال‌سازی اشتراک ماهیانه (۵ ترون)، لطفاً مبلغ را به آدرس زیر واریز کن:\n\n"
            f"<code>{tron_msg}</code>\n\n"
            "پس از واریز، از دستور زیر استفاده کن تا هش تراکنشت ثبت بشه و ما آن را بررسی کنیم:\n"
            "<code>/verify TX_HASH</code>\n\n"
            "🔔 توجه: تا زمانی که اشتراک فعال نشود، می‌توانی روزی یک بار از بخش «اطلاعات ارز» استفاده کنی، اما اطلاعات تکمیلی و سایر فیچرها قفل خواهند بود."
        )
        await update.message.reply_text(msg, parse_mode="HTML")

    # ارسال گزارش به INFO_CHANNEL
    if INFO_CHANNEL:
        try:
            info = f"🔔 کاربر {user_id} ربات را استارت زد.\nاشتراک فعال: {'بله' if subscribed else 'خیر'}\nزمان: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            await context.bot.send_message(chat_id=INFO_CHANNEL, text=info)
        except telegram.error.TelegramError as e:
            print(f"Error sending start info to INFO_CHANNEL: {e}")

# /check
async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    subscribed, days_left = check_subscription_status(user_id)
    if subscribed:
        await update.message.reply_text(f"🟢 اشتراک شما فعال است.\n⏰ حدوداً {days_left} روز باقی مانده.")
    else:
        await update.message.reply_text("❌ شما اشتراک فعال ندارید. برای فعال‌سازی، /start را بزنید و راهنمای پرداخت را دنبال کنید.")

# /verify <tx_hash>
async def verify_tx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text("❌ لطفاً هش تراکنش را به صورت: /verify <tx_hash> ارسال کنید.")
        return
    tx_hash = args[0].strip()
    # ثبت پرداخت در جدول payments با وضعیت pending
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO payments (telegram_id, tx_hash, status) VALUES (%s, %s, %s) RETURNING id, created_at", (user_id, tx_hash, 'pending'))
    rec = cur.fetchone()
    conn.commit()
    payment_id = rec["id"]
    created_at = rec["created_at"]
    cur.close()
    conn.close()

    await update.message.reply_text(f"✅ هش تراکنش شما ثبت شد و در وضعیت بررسی قرار گرفت. شناسه پرداخت: #{payment_id}\nپس از بررسی، وضعیت به شما اعلام می‌شود.")

    # اطلاع به کانال INFO_CHANNEL یا به ادمین‌ها
    notify_msg = (
        f"🟨 تراکنش جدید ثبت شد (منتظر بررسی):\n"
        f"کاربر: {user_id}\n"
        f"payment_id: {payment_id}\n"
        f"tx_hash: <code>{tx_hash}</code>\n"
        f"زمان: {created_at}"
    )
    if INFO_CHANNEL:
        try:
            await context.bot.send_message(chat_id=INFO_CHANNEL, text=notify_msg, parse_mode="HTML")
        except telegram.error.TelegramError as e:
            print(f"Error notifying INFO_CHANNEL about payment: {e}")

# Admin: /approve <telegram_id> [payment_id_optional]
async def approve_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    requester = update.effective_user.id
    if requester not in ADMIN_ID_LIST:
        await update.message.reply_text("❌ دسترسی ندارید.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("❌ استفاده: /approve <telegram_id> [payment_id]")
        return
    try:
        target_telegram_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ telegram_id نامعتبر است.")
        return

    payment_id = None
    if len(args) >= 2:
        try:
            payment_id = int(args[1])
        except ValueError:
            payment_id = None

    # فعال کردن اشتراک
    new_expiry = activate_user_subscription(target_telegram_id, days=30)

    # اگر payment_id داده شده، آن را به approved علامت بزن
    conn = get_db_connection()
    cur = conn.cursor()
    if payment_id:
        cur.execute("UPDATE payments SET status='approved', processed_at=%s WHERE id=%s", (datetime.now(), payment_id))
    # ثبت لاگ در INFO_CHANNEL
    conn.commit()
    cur.close()
    conn.close()

    await update.message.reply_text(f"✅ اشتراک کاربر {target_telegram_id} فعال شد تا {new_expiry.strftime('%Y-%m-%d %H:%M')}.")

    if INFO_CHANNEL:
        try:
            await context.bot.send_message(chat_id=INFO_CHANNEL, text=f"✅ اشتراک کاربر {target_telegram_id} توسط ادمین {requester} فعال شد. (تا {new_expiry})")
        except telegram.error.TelegramError:
            pass

# Admin: /reject <payment_id>
async def reject_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    requester = update.effective_user.id
    if requester not in ADMIN_ID_LIST:
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("❌ استفاده: /reject <payment_id>")
        return
    try:
        payment_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ payment_id نامعتبر است.")
        return

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE payments SET status='rejected', processed_at=%s WHERE id=%s", (datetime.now(), payment_id))
    conn.commit()
    cur.close()
    conn.close()

    await update.message.reply_text(f"❌ پرداخت #{payment_id} رد شد.")
    if INFO_CHANNEL:
        try:
            await context.bot.send_message(chat_id=INFO_CHANNEL, text=f"❌ پرداخت #{payment_id} توسط ادمین {requester} رد شد.")
        except telegram.error.TelegramError:
            pass

# هندل پیام‌های عمومی (جستجوی ارز)
async def crypto_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_api_key
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # اگر کاربر درخواست "📊 وضعیت کلی بازار" را فرستاد، همان تابع قبلی را صدا بزن
    if text == "📊 وضعیت کلی بازار":
        await show_global_market(update)
        return

    # بررسی دسترسی: آیا کاربر اشتراک دارد؟
    subscribed, days_left = check_subscription_status(user_id)
    if not subscribed:
        # کاربر بدون اشتراک: فقط یک بار در روز اجازه دارد اطلاعات ارز را بپرسد
        if has_free_use_today(user_id):
            await update.message.reply_text("⚠️ شما امروز از سهمیه رایگان خود استفاده کرده‌اید. برای دسترسی بیشتر لطفاً اشتراک تهیه کنید.")
            return
        else:
            # ثبت استفاده رایگان امروز
            record_free_use(user_id)
            # ادامه و نمایش اطلاعات (یک بار)
            # (توجه: اطلاعات تکمیلی همچنان قفل خواهند بود)
    # اگر به اینجا رسیدیم یا کاربر مشترک است یا هنوز از سهمیه امروز استفاده نکرده است
    if not current_api_key:
        await update.message.reply_text("⚠️ هیچ کلید CoinMarketCap معتبر تنظیم نشده است.")
        return

    query = text.strip().lower()
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
    params = {"symbol": query.upper(), "convert": "USD"}

    try:
        print(f"Sending request to CoinMarketCap API for coin: {query}")
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "data" not in data or query.upper() not in data["data"]:
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
        circulating_supply = result.get("circulating_supply")
        total_supply = result.get("total_supply")
        max_supply = result.get("max_supply")
        num_pairs = result.get("num_market_pairs")
        rank = result.get("cmc_rank")

        msg = f"""🔍 <b>اطلاعات ارز</b>:\n
🏷️ <b>نام</b>: {name}\n
💱 <b>نماد</b>: {symbol}\n
💵 <b>قیمت</b>: ${safe_number(price)}\n
⏱️ <b>تغییر ۱ ساعته</b>: {safe_number(change_1h, "{:.2f}")}%\n
📊 <b>تغییر ۲۴ ساعته</b>: {safe_number(change_24h, "{:.2f}")}%\n
📅 <b>تغییر ۷ روزه</b>: {safe_number(change_7d, "{:.2f}")}%\n
📈 <b>حجم معاملات ۲۴ساعته</b>: ${safe_number(volume_24h, "{:,.0f}")}\n
💰 <b>ارزش کل بازار</b>: ${safe_number(market_cap, "{:,.0f}")}\n
🛒 <b>تعداد بازارها</b>: {num_pairs}\n
🏅 <b>رتبه بازار</b>: #{rank}
"""
        # اگر کاربر مشترک نیست، دکمه details را نمایش نده
        keyboard = []
        if subscribed:
            keyboard = [[InlineKeyboardButton("📜 نمایش اطلاعات تکمیلی", callback_data=f"details_{symbol}")]]
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)
    except (requests.RequestException, ValueError) as e:
        print(f"Error fetching coin data: {e}")
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات ارز. لطفاً دوباره تلاش کنید.")

# نمایش اطلاعات کلی بازار (بدون تغییر)
async def show_global_market(update: Update):
    global current_api_key
    if not current_api_key:
        await update.message.reply_text("⚠️ هیچ کلید API معتبر در دسترس نیست.")
        return
    url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        total_market_cap = data["data"]["quote"]["USD"]["total_market_cap"]
        total_volume_24h = data["data"]["quote"]["USD"]["total_volume_24h"]
        btc_dominance = data["data"]["btc_dominance"]
        msg = f"""🌐 <b>وضعیت کلی بازار کریپتو</b>:\n
💰 <b>ارزش کل بازار</b>: ${safe_number(total_market_cap, "{:,.0f}")}\n
📊 <b>حجم معاملات ۲۴ساعته</b>: ${safe_number(total_volume_24h, "{:,.0f}")}\n
🟠 <b>دامیننس بیت‌کوین</b>: {safe_number(btc_dominance, "{:.2f}")}%
"""
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        print(f"Global market error: {e}")
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات کلی بازار.")

# پردازش کلیک روی دکمه Inline برای اطلاعات تکمیلی
async def handle_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_api_key
    query = update.callback_query
    await query.answer()
    callback_data = query.data
    if not callback_data.startswith("details_"):
        await query.message.reply_text("⚠️ درخواست نامعتبر.")
        return
    symbol = callback_data[len("details_"):]

    # بررسی دسترسی: فقط کاربرانی که اشتراک فعال دارند می‌توانند این را ببینند
    user_id = query.from_user.id
    subscribed, _ = check_subscription_status(user_id)
    if not subscribed:
        await query.message.reply_text("⚠️ این قسمت فقط برای کاربران دارای اشتراک فعال در دسترس است. برای فعال‌سازی، /start را بزنید.")
        return

    if not current_api_key:
        await query.message.reply_text("⚠️ هیچ کلید API معتبر در دسترس نیست.")
        return

    # درخواست به API برای اطلاعات تکمیلی
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/info"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
    params = {"symbol": symbol}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if "data" not in data or symbol.upper() not in data["data"]:
            await query.message.reply_text(f"❌ اطلاعات تکمیلی برای {symbol} پیدا نشد.")
            return
        coin_data = data["data"][symbol.upper()]
        description = coin_data.get("description") or "ناموجود"
        whitepaper = coin_data.get("urls", {}).get("technical_doc", ["ناموجود"])[0]
        website = coin_data.get("urls", {}).get("website", ["ناموجود"])[0]
        logo = coin_data.get("logo", "ناموجود")
        msg = f"""📜 <b>اطلاعات تکمیلی {coin_data.get('name','')}</b>\n\n
💬 <b>درباره:</b> {description[:1000]}...\n
📄 <b>وایت‌پیپر:</b> {whitepaper}\n
🌐 <b>وب‌سایت:</b> {website}\n
🖼 <b>لوگو:</b> {logo}
"""
        keyboard = [[InlineKeyboardButton("❌ بستن", callback_data=f"close_details_{symbol}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        print(f"Error fetching details for {symbol}: {e}")
        await query.message.reply_text("⚠️ خطا در دریافت اطلاعات تکمیلی، لطفاً دوباره تلاش کنید.")

# پردازش دکمه بستن
async def handle_close_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass

# گزارش مصرف API (هر 2 دقیقه)
async def send_usage_report_to_channel(bot: Bot):
    global current_api_key, current_key_index
    if not REPORT_CHANNEL:
        return
    if not current_api_key:
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL, text="⚠️ هیچ کلید API معتبر در دسترس نیست.", parse_mode="HTML")
        except telegram.error.TelegramError:
            pass
        return
    url = "https://pro-api.coinmarketcap.com/v1/key/info"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        usage = data.get("data", {}).get("usage", {}).get("current_month", {})
        plan = data.get("data", {}).get("plan", {})
        credits_used = usage.get("credits_used", 0)
        credits_total = plan.get("credit_limit", 10000)
        credits_left = credits_total - credits_used
        plan_name = plan.get("name", "Free")
        msg = f"""📊 وضعیت مصرف API:\nپلن: {plan_name}\nکل: {credits_total:,}\nمصرف‌شده: {credits_used:,}\nباقی‌مانده: {credits_left:,}\nکلید فعال: #{current_key_index+1}\nزمان: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""
        await bot.send_message(chat_id=REPORT_CHANNEL, text=msg, parse_mode="HTML")
    except Exception as e:
        print(f"Error sending usage report: {e}")

# گزارش کلی API (هر 5 دقیقه)
async def send_api_summary_report(bot: Bot):
    if not REPORT_CHANNEL:
        return
    if not api_keys:
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL, text="⚠️ هیچ کلید API تنظیم نشده است.", parse_mode="HTML")
        except telegram.error.TelegramError:
            pass
        return
    url = "https://pro-api.coinmarketcap.com/v1/key/info"
    total_credits_used = 0
    total_credits_left = 0
    active_keys = 0
    for key in api_keys:
        try:
            headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": key}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            usage = data.get("data", {}).get("usage", {}).get("current_month", {})
            plan = data.get("data", {}).get("plan", {})
            credits_used = usage.get("credits_used", 0)
            credits_total = plan.get("credit_limit", 10000)
            credits_left = credits_total - credits_used
            total_credits_used += credits_used
            total_credits_left += credits_left
            if credits_left > 0:
                active_keys += 1
        except Exception as e:
            print(f"Error checking API key for summary: {e}")
            continue
    msg = f"""📋 گزارش کلی API:\nتعداد کل کلیدها: {len(api_keys)}\nکلیدهای فعال: {active_keys}\nمصرف‌شده: {total_credits_used:,}\nباقی‌مانده: {total_credits_left:,}\nزمان: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""
    try:
        await bot.send_message(chat_id=REPORT_CHANNEL, text=msg, parse_mode="HTML")
    except telegram.error.TelegramError:
        pass

# تابع اصلی
async def main():
    try:
        print("Initializing Telegram bot...")
        init_db()
        print("Database initialized.")
        app = ApplicationBuilder().token(BOT_TOKEN).build()

        # handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("check", check_subscription))
        app.add_handler(CommandHandler("verify", verify_tx))
        app.add_handler(CommandHandler("approve", approve_payment))
        app.add_handler(CommandHandler("reject", reject_payment))

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, crypto_info))
        app.add_handler(CallbackQueryHandler(handle_details, pattern="^details_"))
        app.add_handler(CallbackQueryHandler(handle_close_details, pattern="^close_details_"))

        await set_bot_commands(app.bot)

        # بررسی و انتخاب کلید API
        await check_and_select_api_key(app.bot)

        print("Bot is running...")
        await app.initialize()
        await app.start()

        # Polling with conflict retry (همان منطق قبلی)
        retry_count = 0
        max_retries = 3
        while retry_count < max_retries:
            try:
                await app.updater.start_polling()
                break
            except telegram.error.Conflict as e:
                retry_count += 1
                print(f"Conflict error occurred. Retry {retry_count}/{max_retries}...")
                await asyncio.sleep(5)
                if retry_count == max_retries:
                    raise e

        # scheduler برای گزارش‌ها
        scheduler = AsyncIOScheduler()
        scheduler.add_job(send_usage_report_to_channel, "interval", minutes=2, args=[app.bot])
        scheduler.add_job(send_api_summary_report, "interval", minutes=5, args=[app.bot])
        scheduler.start()
        print("Schedulers started (API reports).")
        await asyncio.Event().wait()
    except Exception as e:
        print(f"Error starting bot: {e}")
        raise
    finally:
        try:
            await app.stop()
            await app.shutdown()
        except Exception:
            pass

if __name__ == "__main__":
    asyncio.run(main())
