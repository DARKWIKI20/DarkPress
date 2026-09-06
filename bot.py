import os
import asyncio
import logging
from pathlib import Path
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# استفاده از توکن به صورت مستقیم (با قابلیت خواندن از متغیر محیطی)
BOT_TOKEN = os.getenv("BOT_TOKEN", "8812733722:AAEFW8oxPPQYyqrqHGtnvS8fTpu3ATxcDbo")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

TEMP_DIR = Path("/tmp/videos")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

async def compress_video(input_path: Path, output_path: Path) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vcodec", "libx264",
        "-crf", "28",
        "-preset", "veryfast",
        "-threads", "1",
        "-acodec", "aac",
        "-b:a", "128k",
        str(output_path)
    ]
    
    # اجرای ناهمگام FFmpeg برای جلوگیری از فریز شدن ربات
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()
    return process.returncode == 0

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.reply("ویدیو خود را ارسال کنید تا حجم آن را کاهش دهم.")

@dp.message(F.video)
async def handle_video(message: types.Message):
    # بررسی محدودیت 20 مگابایتی تلگرام برای دانلود توسط ربات‌ها
    if message.video.file_size > 20 * 1024 * 1024:
        return await message.reply("⚠️ ربات‌های تلگرام (بدون سرور لوکال) فقط می‌توانند ویدیوهای زیر ۲۰ مگابایت را دریافت کنند.")

    status_msg = await message.reply("📥 در حال دریافت فایل...")
    
    file_id = message.video.file_id
    input_path = TEMP_DIR / f"{file_id}_in.mp4"
    output_path = TEMP_DIR / f"{file_id}_out.mp4"

    try:
        file_info = await bot.get_file(file_id)
        await bot.download_file(file_info.file_path, destination=str(input_path))
        
        await status_msg.edit_text("⚙️ در حال فشرده‌سازی...")
        
        success = await compress_video(input_path, output_path)
        
        if not success:
            raise RuntimeError("FFmpeg compression failed")

        await status_msg.edit_text("📤 در حال ارسال ویدیو...")
        
        video_file = FSInputFile(path=str(output_path))
        await message.reply_video(
            video=video_file,
            caption="✅ فشرده‌سازی با موفقیت انجام شد."
        )
        
    except Exception as e:
        logger.error(f"Error processing video: {e}")
        await message.reply("❌ خطایی در پردازش ویدیو رخ داد.")
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass
            
        if input_path.exists():
            input_path.unlink()
        if output_path.exists():
            output_path.unlink()

async def main():
    logger.info("Bot is starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
