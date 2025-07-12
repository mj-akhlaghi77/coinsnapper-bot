# از تصویر پایه استفاده می‌کنیم
FROM python:3.11-slim

# تنظیم دایرکتوری کاری
WORKDIR /app

# کپی فایل‌ها به داخل کانتینر
COPY bot.py .

# نصب کتابخانه‌های مورد نیاز
RUN pip install python-telegram-bot==20.3

# اجرای فایل
CMD ["python", "bot.py"]
