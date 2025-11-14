# main.py
# نسخهٔ نهایی: مینیمال، دوستانه، گزارش CMC ساعتی با تاریخ شمسی،
# دکمهٔ وضعیت کلی بازار فقط برای مشترکین، دکمهٔ اشتراک/بررسی اشتراک،
# نمایش اطلاعات تکمیلی برای مشترکین و نمایش کانترکت‌ها (درصورت وجود).
# دکمه‌ها در کیبورد پایین ربات (نه inline)


import os
import requests
import jdatetime
from datetime import datetime, timedelta, date
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, Bot, BotCommand,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
import telegram.error
import psycopg2
from psycopg2.extras import DictCursor
from deep_analysis import get_deep_analysis, init_cache_table

# -------------------------
# تنظیمات محیطی
# -------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
TRON_ADDRESS = os.getenv("TRON_ADDRESS")
INFO_CHANNEL = os.getenv("INFO_CHANNEL")      # مثال: -100123...
REPORT_CHANNEL = os.getenv("REPORT_CHANNEL")  # مثال: -100123...
CMC_API_KEY_1 = os.getenv("CMC_API_KEY_1")
CMC_API_KEY_2 = os.getenv("CMC_API_KEY_2")
CMC_API_KEY_3 = os.getenv("CMC_API_KEY_3")

# پشتیبانی از هر دو نام: ADMIN_IDS یا ADMIN_USER_ID
ADMIN_IDS = os.getenv("ADMIN_IDS") or os.getenv("ADMIN_USER_ID")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN تنظیم نشده است.")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL تنظیم نشده است.")

# لیست کلیدهای CMC
api_keys = [k.strip() for k in (CMC_API_KEY_1, CMC_API_KEY_2, CMC_API_KEY_3) if k and k.strip()]
current_key_index = None
current_api_key = None
if api_keys:
    current_key_index = 0
    current_api_key = api_keys[0]

# تبدیل ADMIN_IDS به لیست اعداد
ADMIN_ID_LIST = []
if ADMIN_IDS:
    try:
        for part in ADMIN_IDS.split(","):
            s = part.strip().replace('"', "").replace("'", "")
            if s:
                ADMIN_ID_LIST.append(int(s))
    except Exception:
        print("فرمت ADMIN_IDS اشتباه است. مثال صحیح: 12345678,87654321")
        ADMIN_ID_LIST = []

print("لیست ادمین‌ها:", ADMIN_ID_LIST)

# -------------------------
# دیتابیس
# -------------------------
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            last_free_use DATE,
            subscription_expiry TIMESTAMP,
            notified_3day BOOLEAN DEFAULT FALSE,
            registered_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            tx_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            note TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            processed_at TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("دیتابیس و جداول آماده‌اند.")

# -------------------------
# تاریخ شمسی
# -------------------------
def to_shamsi(dt: datetime) -> str:
    try:
        jdt = jdatetime.datetime.fromgregorian(datetime=dt)
        return jdt.strftime("%Y/%-m/%-d ساعت %H:%M")
    except Exception:
        try:
            jdt = jdatetime.datetime.fromgregorian(datetime=dt)
            return jdt.strftime("%Y/%m/%d ساعت %H:%M")
        except Exception:
            return dt.strftime("%Y-%m-%d %H:%M")

# -------------------------
# مدیریت اشتراک
# -------------------------
def register_user_if_not_exists(telegram_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE telegram_id = %s", (telegram_id,))
    if not cur.fetchone():
        cur.execute("INSERT INTO users (telegram_id) VALUES (%s)", (telegram_id,))
        conn.commit()
    cur.close()
    conn.close()

def activate_user_subscription(telegram_id: int, days: int = 30):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT subscription_expiry FROM users WHERE telegram_id = %s", (telegram_id,))
    rec = cur.fetchone()
    now = datetime.now()
    if rec and rec["subscription_expiry"] and rec["subscription_expiry"] > now:
        new_expiry = rec["subscription_expiry"] + timedelta(days=days)
    else:
        new_expiry = now + timedelta(days=days)
    cur.execute("UPDATE users SET subscription_expiry = %s, notified_3day = FALSE WHERE telegram_id = %s", (new_expiry, telegram_id))
    conn.commit()
    cur.close()
    conn.close()
    return new_expiry

def check_subscription_status(telegram_id: int):
    if telegram_id in ADMIN_ID_LIST:
        return True, 3650
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

# -------------------------
# نمایش و قالب‌بندی
# -------------------------
def safe_number(value, fmt="{:,.2f}"):
    return fmt.format(value) if value is not None else "نامشخص"

# -------------------------
# مدیریت کلیدهای CMC
# -------------------------
async def check_and_select_api_key(bot: Bot):
    global current_api_key, current_key_index
    if not api_keys:
        if REPORT_CHANNEL:
            try:
                await bot.send_message(chat_id=REPORT_CHANNEL, text="هیچ کلید CoinMarketCap تنظیم نشده.", parse_mode="HTML")
            except telegram.error.TelegramError:
                pass
        current_api_key = None
        current_key_index = None
        return False

    url = "https://pro-api.coinmarketcap.com/v1/key/info"
    prev_index = current_key_index
    selected = False
    for idx, key in enumerate(api_keys):
        headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": key}
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            resp.raise_for_status()
            data = resp.json()
            usage = data.get("data", {}).get("usage", {}).get("current_month", {})
            plan = data.get("data", {}).get("plan", {})
            credits_used = usage.get("credits_used", 0)
            credits_total = plan.get("credit_limit", 10000)
            credits_left = credits_total - credits_used
            if credits_left > 0:
                current_api_key = key
                current_key_index = idx
                selected = True
                break
        except Exception as e:
            print(f"Error checking CMC key #{idx+1}: {e}")
            continue

    if prev_index is not None and selected and prev_index != current_key_index and REPORT_CHANNEL:
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL,
                                   text=f"کلید CMC تغییر کرد!\nاز کلید #{prev_index+1} به #{current_key_index+1} سوئیچ شد.\n{to_shamsi(datetime.now())}")
        except telegram.error.TelegramError:
            pass

    return selected

# -------------------------
# گزارش مصرف
# -------------------------
async def send_usage_report_to_channel(bot: Bot):
    if not REPORT_CHANNEL or not api_keys:
        return

    url = "https://pro-api.coinmarketcap.com/v1/key/info"
    total_credits_used = 0
    total_credits_left = 0
    active_keys = 0
    per_key_msgs = []

    for idx, key in enumerate(api_keys):
        headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": key}
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            usage = data.get("usage", {}).get("current_month", {})
            plan = data.get("plan", {})
            credits_used = usage.get("credits_used", 0)
            credits_total = plan.get("credit_limit", 10000)
            plan_name = plan.get("name", "Free")
            credits_left = credits_total - credits_used
            total_credits_used += credits_used
            total_credits_left += credits_left
            if credits_left > 0:
                active_keys += 1
            per_key_msgs.append((idx, plan_name, credits_total, credits_used, credits_left))
        except Exception as e:
            print(f"Error checking key #{idx+1}: {e}")
            per_key_msgs.append((idx, "Error", 0, 0, 0))

    if current_api_key is not None and current_key_index is not None:
        detail = next((item for item in per_key_msgs if item[0] == current_key_index), None)
        if detail:
            plan_name, credits_total, credits_used, credits_left = detail[1], detail[2], detail[3], detail[4]
        else:
            plan_name = "نامشخص"
            credits_total = credits_used = credits_left = 0

        msg_active = f"""وضعیت مصرف API کوین‌مارکت‌کپ:
پلن: {plan_name}
اعتبارات ماهانه: {credits_total:,}
مصرف‌شده: {credits_used:,}
باقی‌مانده: {credits_left:,}
کلید فعال: شماره {current_key_index + 1} ({current_api_key[-6:]})
آخرین بروزرسانی: {to_shamsi(datetime.now())}"""
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL, text=msg_active, parse_mode="HTML")
        except telegram.error.TelegramError:
            pass

    msg_summary = f"""گزارش کلی API کوین‌مارکت‌کپ:
تعداد کلیدها: {len(api_keys)}
کلیدهای فعال: {active_keys}
کل کردیت مصرف‌شده: {total_credits_used:,}
کل کردیت باقی‌مانده: {total_credits_left:,}
آخرین بروزرسانی: {to_shamsi(datetime.now())}"""
    try:
        await bot.send_message(chat_id=REPORT_CHANNEL, text=msg_summary, parse_mode="HTML")
    except telegram.error.TelegramError:
        pass

# -------------------------
# دستورات منو
# -------------------------
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand("start", "شروع ربات"),
        BotCommand("check", "بررسی اشتراک"),
        BotCommand("verify", "ثبت هش پرداخت: /verify <tx_hash>"),
    ]
    await bot.set_my_commands(commands)

# /start — با کیبورد پایین
# /start — دکمه‌ها بر اساس اشتراک
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    register_user_if_not_exists(user_id)
    subscribed, days_left = check_subscription_status(user_id)

    msg = "سلام! اسم یا نماد یه ارز رو بفرست (مثلاً BTC یا بیت‌کوین) تا اطلاعاتشو برات بیارم."

    # ساخت کیبورد بر اساس اشتراک
    if subscribed:
        # فقط برای مشترکین
        keyboard = [
            [KeyboardButton("وضعیت کلی بازار")],
            [KeyboardButton("بررسی اشتراک")]
        ]
    else:
        # برای غیرمشترکین
        keyboard = [
            [KeyboardButton("وضعیت کلی بازار")],
            [KeyboardButton("اشتراک و پرداخت")]
        ]

    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

    try:
        await update.message.reply_text(msg, reply_markup=reply_markup)
    except Exception:
        await update.message.reply_text(msg)

    # گزارش به کانال
    if INFO_CHANNEL:
        try:
            await context.bot.send_message(
                chat_id=INFO_CHANNEL,
                text=f"کاربر <code>{user_id}</code> ربات رو استارت زد.\nاشتراک: {'بله' if subscribed else 'خیر'}\nزمان: {to_shamsi(datetime.now())}",
                parse_mode="HTML"
            )
        except telegram.error.TelegramError:
            pass

# هندلر کلیک روی دکمه‌های کیبورد پایین
# هندلر کلیک روی دکمه‌های کیبورد پایین
async def handle_keyboard_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    subscribed, days_left = check_subscription_status(user_id)

    if text == "وضعیت کلی بازار":
        if not subscribed:
            await update.message.reply_text(
                "برای دیدن وضعیت کلی بازار باید اشتراک داشته باشی.\n"
                "از دکمه «اشتراک و پرداخت» استفاده کن."
            )
            return
        await show_global_market(update, context)
        return

    elif text == "بررسی اشتراک":
        # فقط مشترکین این دکمه رو دارن، پس همیشه اشتراک دارن
        await update.message.reply_text(f"اشتراک فعاله — حدوداً {days_left} روز باقیه.")
        return

    elif text == "اشتراک و پرداخت":
        # فقط غیرمشترکین این دکمه رو دارن
        tron_address = TRON_ADDRESS or "آدرس پرداخت هنوز تنظیم نشده است."
        await update.message.reply_text(
            f"<b>اشتراک ماهیانه (۵ ترون)</b>\n\n"
            f"مبلغ رو به این آدرس واریز کن:\n\n"
            f"<code>{tron_address}</code>\n\n"
            f"بعد از پرداخت، هش تراکنش رو با دستور زیر بفرست:\n"
            f"<code>/verify YOUR_TX_HASH</code>",
            parse_mode="HTML"
        )
        return

# /check
async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    subscribed, days_left = check_subscription_status(user_id)
    if subscribed:
        await update.message.reply_text(f"اشتراک فعاله — حدوداً {days_left} روز باقیه.")
    else:
        await update.message.reply_text("اشتراک فعالی نداری. برای اطلاعات پرداخت /start رو بزن یا از دکمهٔ اشتراک استفاده کن.")

# /verify <tx_hash>
# /verify <tx_hash>
async def verify_tx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args

    if not args:
        await update.message.reply_text(
            "لطفاً هش رو به شکل زیر بفرست:\n"
            "<code>/verify YOUR_TX_HASH_HERE</code>",
            parse_mode="HTML"
        )
        return

    tx_hash = args[0].strip()
    if len(tx_hash) < 30:
        await udpate.message.reply_text("هش تراکنش معتبر نیست. دوباره امتحان کن.")
        return

    # ذخیره در دیتابیس
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO payments (telegram_id, tx_hash, status)
            VALUES (%s, %s, 'pending')
            RETURNING id, created_at
        """, (user_id, tx_hash))
        rec = cur.fetchone()
        payment_id = rec["id"]
        created_at = rec["created_at"]
        conn.commit()
    except Exception as e:
        print(f"خطا در ذخیره پرداخت: {e}")
        await update.message.reply_text("خطا در ثبت تراکنش. بعداً امتحان کن.")
        cur.close()
        conn.close()
        return
    finally:
        cur.close()
        conn.close()

    # پیام به کاربر
    await update.message.reply_text(
        f"هش تراکنش ثبت شد (شناسه: <code>#{payment_id}</code>)\n"
        "منتظر بررسی ادمین باش — به زودی خبرت می‌کنم",
        parse_mode="HTML"
    )

    # ارسال به کانال INFO_CHANNEL
    if INFO_CHANNEL:
        try:
            txt = (
                f"<b>تراکنش جدید ثبت شد</b>\n\n"
                f"کاربر: <code>{user_id}</code>\n"
                f"هش: <code>{tx_hash}</code>\n"
                f"شناسه پرداخت: <code>#{payment_id}</code>\n"
                f"زمان: {to_shamsi(created_at)}\n\n"
                f"ادمین‌ها: از دکمه‌های زیر استفاده کنید"
            )
            keyboard = [
                [
                    InlineKeyboardButton("تأیید", callback_data=f"admin_pay_approve:{payment_id}"),
                    InlineKeyboardButton("رد", callback_data=f"admin_pay_reject:{payment_id}")
                ]
            ]
            await context.bot.send_message(
                chat_id=INFO_CHANNEL,
                text=txt,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except telegram.error.TelegramError as e:
            print(f"خطا در ارسال به کانال: {e}")
            await update.message.reply_text("هش ثبت شد، اما ارسال به ادمین با مشکل مواجه شد.")


# ====================== هندلر ادمین برای تأیید/رد پرداخت ======================
async def admin_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clicker_id = query.from_user.id

    # فقط ادمین‌ها
    if clicker_id not in ADMIN_ID_LIST:
        try:
            await query.edit_message_text("شما دسترسی ادمین ندارید.")
        except:
            pass
        return

    data = query.data
    if ":" not in data:
        return

    action, pid_str = data.split(":", 1)
    try:
        payment_id = int(pid_str)
    except ValueError:
        await query.edit_message_text("شناسه پرداخت نامعتبر است.")
        return

    # دریافت اطلاعات پرداخت
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id, tx_hash, status FROM payments WHERE id = %s", (payment_id,))
    rec = cur.fetchone()

    if not rec:
        cur.close()
        conn.close()
        await query.edit_message_text(f"پرداخت #{payment_id} پیدا نشد.")
        return

    if rec["status"] != "pending":
        cur.close()
        conn.close()
        await query.edit_message_text(f"این پرداخت قبلاً پردازش شده: {rec['status']}")
        return

    payer_id = rec["telegram_id"]
    now = datetime.now()

    # تأیید پرداخت
    if action == "admin_pay_approve":
        new_expiry = activate_user_subscription(payer_id, days=30)
        cur.execute("""
            UPDATE payments SET status='approved', processed_at=%s, note=%s WHERE id=%s
        """, (now, f"تأیید شده توسط ادمین {clicker_id}", payment_id))
        conn.commit()

        try:
            await query.edit_message_text(
                f"پرداخت #{payment_id} تأیید شد\n"
                f"کاربر: <code>{payer_id}</code>\n"
                f"تمدید تا: {to_shamsi(new_expiry)}",
                parse_mode="HTML"
            )
        except:
            pass

        try:
            await context.bot.send_message(
                chat_id=payer_id,
                text=f"پرداختت تأیید شد!\n"
                     f"اشتراک تا {to_shamsi(new_expiry)} فعال شد\n"
                     f"از ربات لذت ببر"
            )
        except:
            pass

    # رد پرداخت
    elif action == "admin_pay_reject":
        cur.execute("""
            UPDATE payments SET status='rejected', processed_at=%s, note=%s WHERE id=%s
        """, (now, f"رد شده توسط ادمین {clicker_id}", payment_id))
        conn.commit()

        try:
            await query.edit_message_text(
                f"پرداخت #{payment_id} رد شد\n"
                f"کاربر: <code>{payer_id}</code>",
                parse_mode="HTML"
            )
        except:
            pass

        try:
            await context.bot.send_message(
                chat_id=payer_id,
                text=f"متأسفانه پرداخت (#{payment_id}) معتبر نبود.\n"
                     f"اگر فکر می‌کنی اشتباه شده، با ادمین تماس بگیر."
            )
        except:
            pass

    cur.close()
    conn.close()
# وضعیت کلی بازار
async def show_global_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.message else update.callback_query.from_user.id
    subscribed, _ = check_subscription_status(user_id)
    if not subscribed:
        await (update.message or update.callback_query.message).reply_text("برای دیدن وضعیت کلی بازار باید اشتراک داشته باشی.")
        return

    if not current_api_key:
        await (update.message or update.callback_query.message).reply_text("کلید CoinMarketCap فعال نیست. بعداً تلاش کن.")
        return

    url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        total_market_cap = data.get("quote", {}).get("USD", {}).get("total_market_cap")
        total_volume_24h = data.get("quote", {}).get("USD", {}).get("total_volume_24h")
        btc_dominance = data.get("btc_dominance")
        active_cryptocurrencies = data.get("active_cryptocurrencies")
        last_updated = data.get("last_updated")
        last_txt = to_shamsi(datetime.fromisoformat(last_updated)) if last_updated else to_shamsi(datetime.now())

        msg = (
            f"وضعیت کلی بازار:\n\n"
            f"ارزش کل بازار: ${safe_number(total_market_cap, '{:,.0f}')}\n"
            f"حجم ۲۴ساعته: ${safe_number(total_volume_24h, '{:,.0f}')}\n"
            f"دامیننس بیت‌کوین: {safe_number(btc_dominance, '{:.2f}')}%\n"
            f"تعداد ارزها: {active_cryptocurrencies}\n"
            f"آخرین بروزرسانی: {last_txt}"
        )
        await (update.message or update.callback_query.message).reply_text(msg)
    except Exception as e:
        print(f"Error show_global_market: {e}")
        await (update.message or update.callback_query.message).reply_text("خطا در دریافت وضعیت کلی بازار.")

# اطلاعات تکمیلی
# ====================== تحلیل عمیق با کش در دیتابیس ======================
async def handle_details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    subscribed, _ = check_subscription_status(user_id)
    symbol = query.data[len("details_"):].upper()

    if not subscribed:
        await query.message.reply_text("برای تحلیل عمیق باید اشتراک داشته باشی.")
        return

    # نمایش لودینگ
    loading = await query.message.reply_text("در حال آماده‌سازی تحلیل عمیق توسط هوش مصنوعی...")

    # ساختار اولیه داده‌ها
    coin_data = {
        "symbol": symbol,
        "name": symbol,
        "description": "",
        "website": "در حال بارگذاری...",
        "whitepaper": "در حال بارگذاری...",
        "contracts": [],
        "price": 0,
        "market_cap": 0,
        "volume_24h": 0
    }

    # دریافت اطلاعات از CMC
    try:
        # اطلاعات پایه
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/info"
        headers = {"X-CMC_PRO_API_KEY": current_api_key}
        resp = requests.get(url, headers=headers, params={"symbol": symbol}, timeout=10)
        if resp.ok:
            data = resp.json()["data"][symbol]
            coin_data.update({
                "name": data.get("name", symbol),
                "description": data.get("description", "")[:3000],
                "website": data.get("urls", {}).get("website", ["ندارد"])[0],
                "whitepaper": data.get("urls", {}).get("technical_doc", ["ندارد"])[0],
            })
            # قراردادها
            for c in data.get("contracts", []):
                addr = c.get("contract_address") or c.get("address")
                net = c.get("platform") or c.get("name")
                if addr:
                    coin_data["contracts"].append({"network": net, "address": addr})

        # قیمت، مارکت کپ، حجم
        qurl = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
        qresp = requests.get(qurl, headers=headers, params={"symbol": symbol}, timeout=8)
        if qresp.ok:
            q = qresp.json()["data"][symbol]["quote"]["USD"]
            coin_data.update({
                "price": q.get("price", 0),
                "market_cap": q.get("market_cap", 0),
                "volume_24h": q.get("volume_24h", 0)
            })
    except Exception as e:
        print(f"خطا در دریافت داده‌های CMC: {e}")

    # دریافت تحلیل عمیق (کش یا API)
    analysis = get_deep_analysis(coin_data)

    # حذف لودینگ
    try:
        await loading.delete()
    except:
        pass

    # ارسال تحلیل
    keyboard = [[InlineKeyboardButton("بستن", callback_data=f"close_details_{symbol.lower()}")]]
    await query.message.reply_text(
        analysis,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True
    )

async def handle_close_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass

# اطلاعات ارز
async def crypto_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_api_key
    user_id = update.effective_user.id
    text = update.message.text.strip()

    register_user_if_not_exists(user_id)
    subscribed, _ = check_subscription_status(user_id)

    if not current_api_key:
        await update.message.reply_text("کلید CoinMarketCap فعال نیست. بعداً تلاش کن.")
        return

    query_symbol = text.strip().lower()
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
    params = {"symbol": query_symbol.upper(), "convert": "USD"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        if "data" not in data or query_symbol.upper() not in data["data"]:
            await update.message.reply_text("ارز پیدا نشد — نام یا نماد دقیق وارد کن.")
            return

        result = data["data"][query_symbol.upper()]
        name = result.get("name")
        symbol = result.get("symbol")
        price = result["quote"]["USD"]["price"]
        change_1h = result["quote"]["USD"].get("percent_change_1h")
        change_24h = result["quote"]["USD"].get("percent_change_24h")
        change_7d = result["quote"]["USD"].get("percent_change_7d")
        market_cap = result["quote"]["USD"].get("market_cap")
        volume_24h = result["quote"]["USD"].get("volume_24h")
        num_pairs = result.get("num_market_pairs")
        rank = result.get("cmc_rank")

        msg = (
            f"اطلاعات {name} ({symbol}):\n\n"
            f"قیمت: ${safe_number(price)}\n"
            f"تغییر ۱ ساعته: {safe_number(change_1h, '{:.2f}')}%\n"
            f"تغییر ۲۴ ساعته: {safe_number(change_24h, '{:.2f}')}%\n"
            f"تغییر ۷ روزه: {safe_number(change_7d, '{:.2f}')}%\n"
            f"حجم ۲۴ساعته: ${safe_number(volume_24h, '{:,.0f}')}\n"
            f"مارکت کپ: ${safe_number(market_cap, '{:,.0f}')}\n"
            f"بازارها: {num_pairs}\n"
            f"رتبه: #{rank}"
        )

        keyboard = [[InlineKeyboardButton("اطلاعات تکمیلی", callback_data=f"details_{symbol}")]]
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        print(f"Error fetching coin: {e}")
        await update.message.reply_text("یه خطایی پیش اومد — دوباره امتحان کن.")

# نوتیفیکیشن تمدید
def check_and_notify_renewals():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        now = datetime.now()
        cur.execute("""
            SELECT telegram_id FROM users
            WHERE subscription_expiry > %s
              AND notified_3day = FALSE
              AND subscription_expiry <= %s
        """, (now, now + timedelta(days=4)))
        rows = cur.fetchall()
        for row in rows:
            cur.execute("UPDATE users SET notified_3day = TRUE WHERE telegram_id = %s", (row["telegram_id"],))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error in check_and_notify_renewals: {e}")

async def send_pending_renewal_notifications(bot: Bot):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT telegram_id, subscription_expiry FROM users WHERE notified_3day = TRUE")
        rows = cur.fetchall()
        now = datetime.now()
        for r in rows:
            if r["subscription_expiry"] and 0 < (r["subscription_expiry"] - now).days <= 3:
                try:
                    await bot.send_message(chat_id=r["telegram_id"], text=f"فقط ۳ روز تا پایان اشتراک مونده! برای تمدید از دکمه اشتراک استفاده کن")
                except:
                    pass
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error in send_pending_renewal_notifications: {e}")

# -------------------------
# راه‌اندازی
# -------------------------
async def main():
    try:
        print("راه‌اندازی ربات...")
        init_db()
        init_cache_table()
        app = ApplicationBuilder().token(BOT_TOKEN).build()

        # هندلرها
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("check", check_subscription))
        app.add_handler(CommandHandler("verify", verify_tx))

        app.add_handler(CallbackQueryHandler(admin_payment_callback, pattern=r"^admin_pay_"))
        app.add_handler(CallbackQueryHandler(handle_details_callback, pattern=r"^details_"))
        app.add_handler(CallbackQueryHandler(handle_close_details, pattern=r"^close_details_"))

        # هندلر دکمه‌های کیبورد پایین
        app.add_handler(MessageHandler(filters.Regex(r"^(وضعیت کلی بازار|بررسی اشتراک|اشتراک و پرداخت)$"), handle_keyboard_buttons))

        # هندلر جستجوی ارز
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"^(وضعیت کلی بازار|بررسی اشتراک|اشتراک و پرداخت)$"), crypto_info))

        await set_bot_commands(app.bot)
        await check_and_select_api_key(app.bot)

        await app.initialize()
        await app.start()

        retry = 0
        while retry < 3:
            try:
                await app.updater.start_polling()
                break
            except telegram.error.Conflict:
                retry += 1
                await asyncio.sleep(3)

        scheduler = AsyncIOScheduler()
        scheduler.add_job(send_usage_report_to_channel, "interval", hours=1, args=[app.bot])
        scheduler.add_job(check_and_notify_renewals, "interval", days=1)
        scheduler.add_job(lambda: asyncio.create_task(send_pending_renewal_notifications(app.bot)), "interval", days=1)
        scheduler.add_job(lambda: asyncio.create_task(check_and_select_api_key(app.bot)), "interval", hours=6)
        scheduler.start()

        print("ربات اجرا شد")
        await asyncio.Event().wait()
    except Exception as e:
        print(f"Error in main: {e}")
        raise
    finally:
        try:
            await app.stop()
            await app.shutdown()
        except Exception:
            pass

if __name__ == "__main__":
    asyncio.run(main())
