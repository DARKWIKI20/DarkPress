import os
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

# دریافت توکن و آدرس از ریل‌وی
BOT_TOKEN = os.getenv("BOT_TOKEN", "8812733722:AAEFW8oxPPQYyqrqHGtnvS8fTpu3ATxcDbo")
LOCAL_SERVER_URL = os.getenv("LOCAL_SERVER_URL") # مقدار پیش‌فرضِ خراب حذف شد

# تصمیم‌گیری هوشمند برای اتصال
if LOCAL_SERVER_URL:
    # اگر آدرس لوکال تنظیم شده باشد (پشتیبانی ۲ گیگابایت)
    session = AiohttpSession(api=TelegramAPIServer.from_base(LOCAL_SERVER_URL, is_local=True))
    bot = Bot(token=BOT_TOKEN, session=session)
    print(f"Connecting to local server: {LOCAL_SERVER_URL}")
else:
    # اگر متغیر پاک شده باشد، بدون کرش به تلگرام اصلی وصل می‌شود
    bot = Bot(token=BOT_TOKEN)
    print("Connecting to official Telegram server")

dp = Dispatcher()

# --- بقیه کدهای ربات شما (هندلرها، توابع فشرده‌سازی و ...) اینجا قرار می‌گیرد ---

async def main():
    # حذف وب‌هوک‌های گیر کرده قبل از استارت
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
