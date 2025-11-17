# technical_analysis.py
import os
import requests
import json
import html
from typing import Tuple, Dict, Any, Optional, List

# ---------- تنظیم و نگهداری secret ----------
TAAPI_SECRET: Optional[str] = None

def set_taapi_secret(value: Optional[str]):
    """
    مقدار TAAPI_SECRET را ست می‌کند. این تابع باید از main.py صدا زده شود.
    """
    global TAAPI_SECRET
    TAAPI_SECRET = value

# ---------- پیکربندی اندیکاتورها ----------
INDICATORS = [
    "ema", "sma", "rsi", "macd", "stoch", "bbands", "supertrend",
    "ichimoku", "adx", "obv"
]
PERIODS = {
    "ema": [50, 200],
    "sma": [50, 200],
    "rsi": [14],
    # بقیه اندیکاتورها پارامتر پیش‌فرض می‌گیرند
}

# ---------- کمکی‌ها ----------
def _escape_html(text: str) -> str:
    """
    امن‌سازی برای ارسال در parse_mode='HTML' تلگرام.
    فقط کارکترهای <,>,& را escape می‌کنیم (html.escape کافیست).
    """
    if text is None:
        return ""
    return html.escape(str(text))

def _format_indicator_value(v: Any) -> str:
    """
    قالب‌بندی ساده‌ی مقدار اندیکاتور برای نمایش.
    """
    if v is None:
        return "نامشخص"
    if isinstance(v, float) or isinstance(v, int):
        # با 4 رقم اعشار نمایش بده در صورت نیاز
        return f"{v:.4f}" if abs(v) < 1000 and isinstance(v, float) else str(v)
    if isinstance(v, dict) or isinstance(v, list):
        return json.dumps(v, ensure_ascii=False)[:200]
    return str(v)

# ---------- دریافت داده‌ها از TAAPI ----------
def get_technical_data(symbol: str, exchange: str = "binance", interval: str = "4h") -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    سعی می‌کند داده‌ها را از endpoint /bulk دریافت کند.
    در صورت خطا، (None, error_message) برمی‌گرداند.
    در صورت موفقیت، یک دیکشنری با کلیدهای ID اندیکاتورها بازمی‌گرداند.
    """
    if not TAAPI_SECRET:
        return None, "TAAPI_SECRET تنظیم نشده است."

    url = "https://api.taapi.io/bulk"
    headers = {"Content-Type": "application/json"}
    queries: List[Dict[str, Any]] = []

    # ساخت کوئری‌ها
    for ind in INDICATORS:
        if ind in ("ema", "sma"):
            for p in PERIODS.get(ind, []):
                queries.append({
                    "indicator": ind,
                    "params": {"period": p},
                    "id": f"{ind.upper()}{p}"
                })
        else:
            queries.append({
                "indicator": ind,
                "id": ind.upper()
            })

    payload = {
        "secret": TAAPI_SECRET,
        "exchange": exchange,
        "symbol": f"{symbol}/USDT",
        "interval": interval,
        "indicators": queries
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
    except requests.exceptions.RequestException as e:
        return None, f"خطا در اتصال به TAAPI: {e}"

    # وضعیت‌های احتمالی پاسخ
    status = resp.status_code
    text = resp.text
    try:
        data = resp.json()
    except Exception:
        return None, f"پاسخ TAAPI قابل‌پارس نیست: HTTP {status} — {text[:200]}"

    # اگر بدنهٔ خطا دارد
    if isinstance(data, dict) and data.get("error"):
        return None, f"خطای TAAPI: {data.get('error')}"

    # بعضی مواقع TAAPI لیستی از نتایج یا کلید data را برمی‌گرداند
    results = {}
    # حالت 1: داده لیستی از آیتم‌ها هست (هر آیتم دارای id و result)
    if isinstance(data, list):
        for item in data:
            try:
                iid = item.get("id") or item.get("indicator")
                res = item.get("result")
                # بعضی اندیکاتورها object با value دارند
                if isinstance(res, dict) and "value" in res:
                    results[iid] = res
                else:
                    results[iid] = {"value": res}
            except Exception:
                continue
        if results:
            return results, None

    # حالت 2: TAAPI ممکن است دیکشنری با key "data" یا "result" بازگرداند
    if isinstance(data, dict):
        # حالت: data: [ ... ]
        if "data" in data and isinstance(data["data"], list):
            for item in data["data"]:
                try:
                    iid = item.get("id") or item.get("indicator")
                    res = item.get("result")
                    if isinstance(res, dict) and "value" in res:
                        results[iid] = res
                    else:
                        results[iid] = {"value": res}
                except Exception:
                    continue
            if results:
                return results, None

        # حالت: result single object
        if "result" in data:
            r = data["result"]
            if isinstance(r, dict):
                for k, v in r.items():
                    # v ممکن است dict یا عدد باشد
                    if isinstance(v, dict) and "value" in v:
                        results[k] = v
                    else:
                        results[k] = {"value": v}
                if results:
                    return results, None

    # اگر تا اینجا داده‌ای ننشست، سعی کن ساختارهای شناخته‌شده را ببینی
    # به عنوان fallback، اگر کل payload در 'text' یه لیست json هست، سعی کنیم parse کنیم
    if isinstance(data, dict) and not results:
        # تلاش برای استخراج با id های مورد انتظار
        for q in queries:
            iid = q.get("id")
            # در برخی موارد TAAPI کل داده را زیر کلید iid قرار می‌دهد
            if iid in data:
                results[iid] = {"value": data[iid]}
    if results:
        return results, None

    # هیچ داده‌ای پیدا نشد — برگرداندن پیام مناسب برای لاگ
    return None, f"پاسخ TAAPI ساختار غیرمنتظره‌ای داشت: {json.dumps(data)[:400]}"

# ---------- ساخت پرامپت برای GPT ----------
def generate_technical_prompt(symbol: str, data: Dict[str, Any], interval: str = "4h") -> str:
    """
    ساخت یک پرامپت خلاصه برای ارسال به تابع تحلیل عمیق (deep_analysis).
    ما چند مقدار کلیدی را استخراج می‌کنیم و در پرامپت قرار می‌دهیم.
    """
    ema50 = _format_indicator_value(data.get("EMA50", {}).get("value"))
    ema200 = _format_indicator_value(data.get("EMA200", {}).get("value"))
    rsi = _format_indicator_value(data.get("RSI", {}).get("value"))
    macd = _format_indicator_value(data.get("MACD", {}).get("value"))
    supertrend = _format_indicator_value(data.get("SUPERTREND", {}).get("value"))
    price = _format_indicator_value(data.get("CLOSE", {}).get("value") if data.get("CLOSE") else None)

    prompt_lines = [
        f"نماد: {symbol}/USDT",
        f"تایم‌فریم: {interval}",
        "",
        "داده‌های دریافتی از TAAPI:",
        f"- قیمت (در صورت وجود): {price}",
        f"- EMA50: {ema50}",
        f"- EMA200: {ema200}",
        f"- RSI(14): {rsi}",
        f"- MACD: {macd}",
        f"- Supertrend: {supertrend}",
        "",
        "لطفاً یک تحلیل تکنیکال کوتاه، واضح و عملیاتی به زبان فارسی بنویس که شامل:",
        "1) روند کلی (صعودی/نزولی/رنج)",
        "2) وضعیت قیمت نسبت به EMA50 و EMA200",
        "3) وضعیت RSI و احتمال اشباع خرید/فروش",
        "4) سیگنال MACD",
        "5) وضعیت Supertrend",
        "6) سطوح کلیدی حمایت/مقاومت (حداقل 2 سطح)",
        "7) استراتژی پیشنهادی: ورود، استاپ، تارگت و ریسک (RR)",
    ]
    return "\n".join(prompt_lines)

# ---------- تابع اصلی تحلیل ----------
async def get_technical_analysis(symbol: str, context=None, exchange: str = "binance", interval: str = "4h") -> str:
    """
    تابع اصلی که توسط main.py فراخوانی می‌شود.
    خروجی: رشته HTML-escaped مناسب ارسال با parse_mode='HTML'.
    در صورت خطا، یک پیام مناسب HTML باز می‌گرداند.
    """
    # 1) دریافت داده‌ها
    data, error = get_technical_data(symbol, exchange=exchange, interval=interval)
    if error:
        # پیغام خطا را به کاربر برگردان
        return (
            "<b>تحلیل تکنیکال در دسترس نیست</b>\n"
            f"<code>{_escape_html(error)}</code>"
        )

    # 2) ساخت پرامپت و صدا زدن فانکشن تحلیل عمیق (که در پروژه وجود دارد)
    prompt = generate_technical_prompt(symbol, data, interval=interval)

    # اگر پروژه‌ات تابعی متفاوت دارد، فرض ما این است که تو فایل deep_analysis
    # تابع call_openai_analysis(fake_coin, context) وجود دارد (طبق ساختار قبلی تو).
    try:
        fake_coin = {"symbol": symbol, "name": symbol, "description": prompt}
        # import محلی — اگر فایل deep_analysis تو نام متغیر یا فانکشنی متفاوت داشت،
        # آن را مطابق تغییرات پروژه‌ات تنظیم کن.
        from deep_analysis import call_openai_analysis
        raw_analysis = call_openai_analysis(fake_coin, context)
        if not raw_analysis:
            return "<b>تحلیل تکنیکال آماده نیست</b>\n<code>خروجی GPT خالی بود.</code>"
    except Exception as e:
        return (
            "<b>خطا در تولید تحلیل</b>\n"
            f"<code>{_escape_html(str(e)[:400])}</code>"
        )

    # 3) تبدیل خروجی GPT به HTML-safe
    # فرض می‌کنیم raw_analysis یک متن فارسی قالب‌بندی شده است.
    safe = _escape_html(raw_analysis)

    # 4) بهبود جزئی قالب: خطوط شماره‌دار را بولد کن (مثلاً "1. روند کلی")
    lines = safe.splitlines()
    out_lines = []
    for ln in lines:
        s = ln.strip()
        if s and len(s) > 1 and s[0].isdigit() and s[1] == '.':
            # بولد کن عنوان شماره‌دار تا خواناتر شود
            # توجه: html.escape قبلاً اجرا شده پس از <b> استفاده می‌کنیم
            title = s
            out_lines.append(f"<b>{title}</b>")
        else:
            out_lines.append(ln)
    final_text = "\n".join(out_lines)

    # 5) پیش‌نمایش کوتاه از داده‌های کلیدی در بالا (اختیاری، اما مفید)
    try:
        preview_items = []
        for key in ("EMA50", "EMA200", "RSI", "MACD", "SUPERTREND"):
            val = data.get(key, {}).get("value")
            preview_items.append(f"{key}: {_escape_html(_format_indicator_value(val))}")
        preview = " | ".join(preview_items)
        header = f"<b>تحلیل تکنیکال { _escape_html(symbol.upper()) } — تایم‌فریم {interval}</b>\n<code>{preview}</code>\n\n"
    except Exception:
        header = f"<b>تحلیل تکنیکال { _escape_html(symbol.upper()) }</b>\n\n"

    return header + final_text
