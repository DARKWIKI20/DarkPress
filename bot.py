import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

# تنظیم لاگ‌ها برای نمایش پیام‌های دریافتی در Railway
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8812733722:AAEFW8oxPPQYyqrqHGtnvS8fTpu3ATxcDbo")
LOCAL_SERVER_URL = os.getenv("LOCAL_SERVER_URL")

if LOCAL_SERVER_URL:
    session = AiohttpSession(api=TelegramAPIServer.from_base(LOCAL_SERVER_URL, is_local=True))
    bot = Bot(token=BOT_TOKEN, session=session)
    print(f"Connecting to local server: {LOCAL_SERVER_URL}")
else:
    bot = Bot(token=BOT_TOKEN)
    print("Connecting to official Telegram server")

dp = Dispatcher()

# هندلر دستور استارت
@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer("سلام! ربات DarkPress فعال است و پیام شما دریافت شد.")

async def main():
    # پاک‌سازی وضعیت وب‌هوک و آپدیت‌های معلق قبلی
    await bot.delete_webhook(drop_pending_updates=True)
    print("Webhook cleared. Starting polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
