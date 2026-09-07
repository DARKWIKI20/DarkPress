import os
import re
import time
import asyncio
import logging
import subprocess
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8812733722:AAEFW8oxPPQYyqrqHGtnvS8fTpu3ATxcDbo")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

class VideoConfig(StatesGroup):
    waiting_for_config = State()

def generate_progress_bar(percent: int) -> str:
    """نوار پیشرفت کلاسیک و بدون ایموجی"""
    filled = int(round(percent / 10))
    bar = "█" * filled + "▒" * (10 - filled)
    return f"[{bar}] {percent}%"

async def get_video_duration(file_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, _ = await proc.communicate()
        return float(stdout.decode().strip())
    except Exception:
        return 0.0

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer(
        "به ربات فشرده‌ساز خوش آمدید.\n\n"
        "جهت شروع، ویدیوی خود را (حداکثر ۵۰ مگابایت) ارسال کنید تا گزینه‌های پردازش نمایش داده شوند."
    )

@dp.message(F.video | F.document)
async def handle_video(message: types.Message, state: FSMContext):
    video = message.video or (message.document if message.document and message.document.mime_type and message.document.mime_type.startswith("video/") else None)
    
    if not video:
        return await message.answer("فرمت فایل نامعتبر است. لطفاً یک ویدیو ارسال کنید.")
    if video.file_size > 50 * 1024 * 1024:
        return await message.answer("حجم ویدیو بیشتر از سقف مجاز (۵۰ مگابایت) است.")

    # ذخیره اطلاعات فایل در State
    await state.update_data(file_id=video.file_id, msg_id=message.message_id, file_size=video.file_size)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="Original", callback_data="process_orig_h264")
    builder.button(text="1080p", callback_data="process_1080_h264")
    builder.button(text="720p", callback_data="process_720_h264")
    builder.button(text="480p", callback_data="process_480_h264")
    builder.button(text="720p (H.265/HEVC)", callback_data="process_720_h265")
    builder.adjust(2, 2, 1)

    await message.answer(
        "تنظیمات خروجی را انتخاب کنید:",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("process_"))
async def process_callback(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("file_id"):
        return await callback.message.edit_text("نشست منقضی شده است. لطفاً ویدیو را مجدداً ارسال کنید.")
    
    await state.clear()
    
    _, res, codec_type = callback.data.split("_")
    file_id = data["file_id"]
    msg_id = data["msg_id"]
    initial_size = data["file_size"]
    
    status_msg = await callback.message.edit_text("در حال بارگیری از سرور...\n" + generate_progress_bar(0))
    
    input_path = os.path.join(DOWNLOAD_DIR, f"in_{msg_id}.mp4")
    output_path = os.path.join(DOWNLOAD_DIR, f"out_{msg_id}.mp4")

    try:
        # مرحله دانلود
        file_info = await bot.get_file(file_id)
        await bot.download_file(file_info.file_path, destination=input_path)

        total_duration = await get_video_duration(input_path)
        await status_msg.edit_text("آماده‌سازی موتور پردازش...\n" + generate_progress_bar(0))

        # تنظیمات FFmpeg بر اساس انتخاب کاربر
        codec = "libx265" if codec_type == "h265" else "libx264"
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", codec, "-crf", "28", "-preset", "faster",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "128k"
        ]
        
        if res != "orig":
            cmd.extend(["-vf", f"scale=-2:{res}"])
            
        cmd.append(output_path)

        process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        last_update_time = time.time()
        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")

        while True:
            line = await process.stderr.readline()
            if not line:
                break
            
            decoded_line = line.decode(errors="ignore")
            match = time_pattern.search(decoded_line)
            
            if match and total_duration > 0:
                h, m, s = map(float, match.groups())
                current_time = h * 3600 + m * 60 + s
                percent = min(100, int((current_time / total_duration) * 100))

                if time.time() - last_update_time > 2.0:
                    try:
                        await status_msg.edit_text(f"در حال پردازش:\n{generate_progress_bar(percent)}")
                        last_update_time = time.time()
                    except: pass

        await process.wait()

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return await status_msg.edit_text("خطا: پردازش متوقف شد. فایل نامعتبر است.")

        await status_msg.edit_text("پردازش موفق. در حال آپلود...")

        final_size = os.path.getsize(output_path)
        reduction = max(0, int(((initial_size - final_size) / initial_size) * 100))

        caption = (
            f"عملیات پایان یافت.\n\n"
            f"حجم اصلی: {initial_size / (1024*1024):.2f} MB\n"
            f"حجم نهایی: {final_size / (1024*1024):.2f} MB\n"
            f"صرفه‌جویی: {reduction}%"
        )

        compressed_file = types.FSInputFile(output_path, filename=f"video_{msg_id}.mp4")
        await bot.send_video(
            chat_id=callback.message.chat.id,
            video=compressed_file,
            caption=caption,
            supports_streaming=True
        )
        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"خطای سیستمی:\n{e}")
    finally:
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
