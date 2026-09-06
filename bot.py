import os
import asyncio
import logging
from pathlib import Path
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8812733722:AAEFW8oxPPQYyqrqHGtnvS8fTpu3ATxcDbo")
LOCAL_SERVER_URL = os.getenv("LOCAL_SERVER_URL", "http://tg-api.railway.internal:8081")

session = AiohttpSession(api=TelegramAPIServer.from_base(LOCAL_SERVER_URL, is_local=True))
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

TEMP_DIR = Path("/tmp/videos")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

user_tasks = {}

async def compress_video(input_path: Path, output_path: Path, codec: str, quality: str) -> bool:
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vcodec", codec, "-crf", "28", "-preset", "veryfast", "-threads", "1",
        "-acodec", "aac", "-b:a", "128k"
    ]
    if quality == "720":
        cmd.extend(["-vf", "scale=-2:720"])
    elif quality == "480":
        cmd.extend(["-vf", "scale=-2:480"])
    cmd.append(str(output_path))
    
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await process.communicate()
    return process.returncode == 0

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.reply("لطفاً فایل ویدیویی خود را (حتی به صورت Document) ارسال کنید تا پردازش را شروع کنیم.")

@dp.message(F.video | F.document)
async def handle_video(message: types.Message):
    doc = message.video or message.document
    if message.document and not (doc.mime_type and doc.mime_type.startswith('video/')):
        return await message.reply("لطفاً فقط فایل ویدیویی ارسال کنید.")
        
    user_tasks[message.from_user.id] = {"file_id": doc.file_id, "file_name": getattr(doc, 'file_name', 'video.mp4')}
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="H.264 (استاندارد و سریع)", callback_data="codec_libx264")],
        [InlineKeyboardButton(text="H.265 (حجم بسیار کمتر)", callback_data="codec_libx265")]
    ])
    await message.reply("⚙️ نوع انکودر را انتخاب کنید:", reply_markup=kb)

@dp.callback_query(F.data.startswith("codec_"))
async def set_codec(call: CallbackQuery):
    user_id = call.from_user.id
    if user_id not in user_tasks: return await call.answer("خطا! دوباره ویدیو را بفرستید.")
    
    user_tasks[user_id]["codec"] = call.data.replace("codec_", "")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="کیفیت اصلی (بدون تغییر)", callback_data="qual_orig")],
        [InlineKeyboardButton(text="کاهش ابعاد به 720p", callback_data="qual_720")],
        [InlineKeyboardButton(text="کاهش ابعاد به 480p", callback_data="qual_480")]
    ])
    await call.message.edit_text("📺 کیفیت خروجی را تعیین کنید:", reply_markup=kb)

@dp.callback_query(F.data.startswith("qual_"))
async def start_processing(call: CallbackQuery):
    user_id = call.from_user.id
    if user_id not in user_tasks: return await call.answer("خطا! دوباره ویدیو را بفرستید.")
    
    task = user_tasks.pop(user_id)
    task["quality"] = call.data.replace("qual_", "")
    
    status_msg = await call.message.edit_text("📥 در حال دریافت فایل روی سرور خصوصی...")
    
    input_path = TEMP_DIR / f"{task['file_id']}_in.mp4"
    output_path = TEMP_DIR / f"{task['file_id']}_out.mp4"

    try:
        file_info = await bot.get_file(task["file_id"])
        await bot.download_file(file_info.file_path, destination=str(input_path))
        
        await status_msg.edit_text(f"⚙️ در حال فشرده‌سازی...\nانکودر: {task['codec']}\nکیفیت: {task['quality']}")
        success = await compress_video(input_path, output_path, task["codec"], task["quality"])
        if not success: raise RuntimeError("FFmpeg error")

        await status_msg.edit_text("📤 در حال ارسال ویدیوی بهینه‌شده...")
        await call.message.reply_video(video=FSInputFile(path=str(output_path)), caption="✅ عملیات موفق!")
    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text("❌ مشکلی در پردازش پیش آمد.")
    finally:
        if input_path.exists(): input_path.unlink()
        if output_path.exists(): output_path.unlink()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
