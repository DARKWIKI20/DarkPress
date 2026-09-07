import os
import re
import time
import asyncio
import logging
import subprocess
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8812733722:AAEFW8oxPPQYyqrqHGtnvS8fTpu3ATxcDbo")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def generate_progress_bar(percent: int) -> str:
    """ساخت ظاهر گرافیکی درصد پیشرفت با کاراکترهای یونیکد"""
    total_blocks = 10
    filled = int(round(percent / 10))
    bar = "🟩" * filled + "⬜" * (total_blocks - filled)
    return f"{bar} {percent}%"


async def get_video_duration(file_path: str) -> float:
    """دریافت دقیق طول مدت زمان ویدیو از طریق ffprobe"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        return float(stdout.decode().strip())
    except Exception:
        return 0.0


@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer(
        "🎬 **به ربات DarkPress Video Optimizer خوش آمدید!**\n\n"
        "⚡ برای فشرده‌سازی خودکار و حفظ رزولوشن، کافیست ویدیوی خود را (حداکثر ۵۰ مگابایت) بفرستید.\n"
        "🔧 خروجی با کدک استاندارد `H.264` و متادیتای `faststart` بهینه‌سازی خواهد شد."
    )


@dp.message(F.video | F.document)
async def handle_video(message: types.Message):
    video = message.video or (
        message.document
        if message.document and message.document.mime_type and message.document.mime_type.startswith("video/")
        else None
    )

    if not video:
        await message.answer("⚠️ لطفاً یک فایل ویدیویی ارسال کنید.")
        return

    if video.file_size > 50 * 1024 * 1024:
        await message.answer("❌ **خطای محدودیت حجم:** حجم ویدیو بیشتر از سقف ۵۰ مگابایت سرور اصلی است.")
        return

    status_msg = await message.answer("📥 **در حال دریافت فایل از تلگرام...**\n" + generate_progress_bar(0))

    input_path = os.path.join(DOWNLOAD_DIR, f"input_{message.message_id}.mp4")
    output_path = os.path.join(DOWNLOAD_DIR, f"compressed_{message.message_id}.mp4")

    try:
        # مرحله اول: دانلود
        file_info = await bot.get_file(video.file_id)
        await bot.download_file(file_info.file_path, destination=input_path)

        # دریافت مدت زمان فایل برای محاسبه زنده FFmpeg
        total_duration = await get_video_duration(input_path)

        await status_msg.edit_text("⚙️ **شروع پردازش و کاهش حجم ویدیو...**\n" + generate_progress_bar(0))

        # مرحله دوم: پردازش و فشرده‌سازی FFmpeg
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-c:v", "libx264",
            "-crf", "28",
            "-preset", "faster",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-c:a", "aac",
            "-b:a", "128k",
            output_path
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # حلقه خواندن خروجی FFmpeg برای ساخت نوار پیشرفت زنده
        last_update_time = time.time()
        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")

        while True:
            line = await process.stderr.readline()
            if not line:
                break

            decoded_line = line.decode(errors="ignore")
            match = time_pattern.search(decoded_line)

            if match and total_duration > 0:
                hours, minutes, seconds = map(float, match.groups())
                current_time = hours * 3600 + minutes * 60 + seconds
                percent = min(100, int((current_time / total_duration) * 100))

                # تلگرام محدودیت ادیت پیام دارد؛ ویرایش را روی هر ۲ ثانیه کنترل می‌کنیم
                if time.time() - last_update_time > 2.0:
                    try:
                        await status_msg.edit_text(
                            f"⚙️ **درحال فشرده‌سازی:**\n{generate_progress_bar(percent)}"
                        )
                        last_update_time = time.time()
                    except Exception:
                        pass

        await process.wait()

        # تأیید سلامت فایل خروجی
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            await status_msg.edit_text("❌ خطا: فایل خروجی خراب شد یا پردازش با شکست مواجه گردید.")
            return

        await status_msg.edit_text("📤 **در حال بارگذاری فایل نهایی در تلگرام...**")

        initial_size_mb = video.file_size / (1024 * 1024)
        compressed_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        reduction_rate = max(0, int(((initial_size_mb - compressed_size_mb) / initial_size_mb) * 100))

        caption_text = (
            "✅ **پردازش با موفقیت انجام شد!**\n\n"
            f"📊 **حجم اولیه:** `{initial_size_mb:.2f} MB`\n"
            f"📉 **حجم بهینه‌شده:** `{compressed_size_mb:.2f} MB`\n"
            f"🚀 **میزان کاهش:** `{reduction_rate}%`"
        )

        compressed_file = types.FSInputFile(
            output_path,
            filename=f"compressed_{message.message_id}.mp4"
        )

        await message.answer_video(
            video=compressed_file,
            caption=caption_text,
            supports_streaming=True
        )

        await status_msg.delete()

    except Exception as e:
        logging.error(f"Error: {e}")
        await status_msg.edit_text(f"⚠️ خطایی رخ داد:\n`{e}`")

    finally:
        # پاک‌سازی فایل‌های موقت هارد سرور
        if os.path.exists(input_path):
            os.remove(input_path)
        if os.path.exists(output_path):
            os.remove(output_path)


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    print("Bot is up and running on official servers.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
