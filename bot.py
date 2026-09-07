import os
import asyncio
import logging
import subprocess
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8812733722:AAEFW8oxPPQYyqrqHGtnvS8fTpu3ATxcDbo")

# اتصال مستقیم به سرورهای رسمی تلگرام (حداکثر ۵۰ مگابایت)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer(
        "سلام! به ربات DarkPress خوش آمدید.\n"
        "یک ویدیو تا حجم ۵۰ مگابایت ارسال کنید تا با کیفیت بهینه فشرده‌سازی شود."
    )

@dp.message(F.video | F.document)
async def handle_video(message: types.Message):
    video = message.video or (message.document if message.document and message.document.mime_type and message.document.mime_type.startswith("video/") else None)
    
    if not video:
        await message.answer("لطفاً یک فایل ویدیویی ارسال کنید.")
        return

    # بررسی سقف حجم دانلود سرور رسمی تلگرام (۵۰ مگابایت)
    if video.file_size > 50 * 1024 * 1024:
        await message.answer("⚠️ حجم فایل بیش از ۵۰ مگابایت است. سرور رسمی تلگرام اجازه دانلود مستقیم این فایل را نمی‌دهد.")
        return

    status_msg = await message.answer("در حال دانلود ویدیو...")

    input_path = os.path.join(DOWNLOAD_DIR, f"input_{message.message_id}.mp4")
    output_path = os.path.join(DOWNLOAD_DIR, f"compressed_{message.message_id}.mp4")

    try:
        # دانلود فایل از سرور تلگرام
        file_info = await bot.get_file(video.file_id)
        await bot.download_file(file_info.file_path, destination=input_path)

        await status_msg.edit_text("ویدیو دریافت شد. در حال فشرده‌سازی (FFmpeg)...")

        # دستور فشرده‌سازی با کدک H.264 و CRF 28 برای کاهش چشمگیر حجم
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vcodec", "libx264",
            "-crf", "28",
            "-preset", "faster",
            "-acodec", "aac",
            "-b:a", "128k",
            output_path
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        await process.communicate()

        if not os.path.exists(output_path):
            await status_msg.edit_text("خطا در پردازش ویدیو رخ داد.")
            return

        await status_msg.edit_text("فشرده‌سازی انجام شد. در حال ارسال...")

        # ارسال فایل فشرده‌شده
        compressed_file = types.FSInputFile(output_path)
        await message.answer_video(
            video=compressed_file,
            caption=f"فشرده‌سازی انجام شد!\nحجم اولیه: {video.file_size // (1024 * 1024)}MB\nحجم نهایی: {os.path.getsize(output_path) // (1024 * 1024)}MB"
        )
        await status_msg.delete()

    except Exception as e:
        logging.error(f"Error: {e}")
        await status_msg.edit_text(f"خطایی رخ داد: {e}")

    finally:
        # پاک‌سازی فایل‌های موقت
        if os.path.exists(input_path):
            os.remove(input_path)
        if os.path.exists(output_path):
            os.remove(output_path)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    print("Connecting to official Telegram server. Polling started...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
