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
ADMIN_ID = 6616272875  # آیدی ادمین برای دریافت لاگ‌ها

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

class VideoConfig(StatesGroup):
    configuring = State()

def generate_progress_bar(percent: int) -> str:
    filled = int(round(percent / 10))
    bar = "█" * filled + "▒" * (10 - filled)
    return f"[{bar}] {percent}%"

async def get_video_duration(file_path: str) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, _ = await proc.communicate()
        return float(stdout.decode().strip())
    except Exception:
        return 0.0

def get_config_keyboard(res: str, codec: str):
    builder = InlineKeyboardBuilder()
    
    r_orig = "اصلی ✅" if res == "orig" else "اصلی"
    r_1080 = "1080p ✅" if res == "1080" else "1080p"
    r_720 = "720p ✅" if res == "720" else "720p"
    r_480 = "480p ✅" if res == "480" else "480p"
    
    builder.button(text=r_orig, callback_data="set_res_orig")
    builder.button(text=r_1080, callback_data="set_res_1080")
    builder.button(text=r_720, callback_data="set_res_720")
    builder.button(text=r_480, callback_data="set_res_480")
    
    c_264 = "H.264 ✅" if codec == "h264" else "H.264"
    c_265 = "H.265 ✅" if codec == "h265" else "H.265"
    
    builder.button(text=c_264, callback_data="set_codec_h264")
    builder.button(text=c_265, callback_data="set_codec_h265")
    
    builder.button(text="🚀 شروع", callback_data="start_process")
    builder.button(text="❌ لغو", callback_data="cancel_process")
    
    builder.adjust(4, 2, 2)
    return builder.as_markup()

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer("🎬 ویدیوی خود را (حداکثر ۵۰ مگابایت) بفرستید تا پنل تنظیمات باز شود.")

@dp.message(F.video | F.document)
async def handle_video(message: types.Message, state: FSMContext):
    video = message.video or (message.document if message.document and message.document.mime_type and message.document.mime_type.startswith("video/") else None)
    
    if not video:
        return await message.answer("⚠️ لطفاً فقط فایل ویدیویی ارسال کنید.")
    if video.file_size > 50 * 1024 * 1024:
        return await message.answer("❌ حجم فایل بیشتر از ۵۰ مگابایت است.")

    await state.set_state(VideoConfig.configuring)
    await state.update_data(file_id=video.file_id, msg_id=message.message_id, file_size=video.file_size, res="720", codec="h264")
    
    await message.answer("⚙️ **تنظیمات خروجی را انتخاب کنید:**", reply_markup=get_config_keyboard("720", "h264"))

@dp.callback_query(F.data == "cancel_process")
async def cancel_process(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ عملیات لغو شد.")

@dp.callback_query(F.data.startswith("set_"))
async def update_config(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    if not data:
        return await callback.message.edit_text("⏳ نشست منقضی شده، ویدیو را دوباره ارسال کنید.")
    
    action = callback.data.split("_")
    if action[1] == "res":
        await state.update_data(res=action[2])
    elif action[1] == "codec":
        await state.update_data(codec=action[2])
        
    new_data = await state.get_data()
    try:
        await callback.message.edit_reply_markup(reply_markup=get_config_keyboard(new_data["res"], new_data["codec"]))
    except: pass

@dp.callback_query(F.data == "start_process")
async def start_process(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    if not data:
        return await callback.message.edit_text("⏳ نشست منقضی شده است. لطفاً ویدیو را دوباره بفرستید.")
    
    res, codec, file_id, msg_id, initial_size = data["res"], data["codec"], data["file_id"], data["msg_id"], data["file_size"]
    await state.clear()
    
    status_msg = await callback.message.edit_text("📥 در حال دریافت فایل...")
    input_path = os.path.join(DOWNLOAD_DIR, f"in_{msg_id}.mp4")
    output_path = os.path.join(DOWNLOAD_DIR, f"out_{msg_id}.mp4")

    try:
        file_info = await bot.get_file(file_id)
        await bot.download_file(file_info.file_path, destination=input_path)
        total_duration = await get_video_duration(input_path)
        await status_msg.edit_text("🔧 آماده‌سازی موتور...")

        v_codec = "libx265" if codec == "h265" else "libx264"
        scale_filter = "scale=trunc(iw/2)*2:trunc(ih/2)*2" if res == "orig" else f"scale=-2:{res}"

        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", v_codec, "-vf", scale_filter,
            "-crf", "28", "-preset", "faster",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "128k", "-sn",
            output_path
        ]

        process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        last_update_time, time_pattern = time.time(), re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")

        while True:
            line = await process.stderr.readline()
            if not line: break
            
            match = time_pattern.search(line.decode(errors="ignore"))
            if match and total_duration > 0:
                h, m, s = map(float, match.groups())
                current_time = h * 3600 + m * 60 + s
                percent = min(100, int((current_time / total_duration) * 100))

                if time.time() - last_update_time > 2.0:
                    try:
                        await status_msg.edit_text(f"⚙️ پردازش:\n{generate_progress_bar(percent)}")
                        last_update_time = time.time()
                    except: pass

        await process.wait()

        if process.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return await status_msg.edit_text("❌ خطا در موتور پردازش.")

        final_size = os.path.getsize(output_path)
        reduction = max(0, int(((initial_size - final_size) / initial_size) * 100))
        
        await status_msg.edit_text("📤 در حال آپلود...")

        # ارسال فایل به کاربر و ذخیره پیام
        sent_video_msg = await bot.send_video(
            chat_id=callback.message.chat.id,
            video=types.FSInputFile(output_path, filename=f"video_{msg_id}.mp4"),
            caption=f"✅ **انجام شد**\nحجم قبل: `{initial_size / (1024*1024):.2f} MB`\nحجم جدید: `{final_size / (1024*1024):.2f} MB`\nکاهش: `{reduction}%`",
            supports_streaming=True
        )
        await status_msg.delete()

        # --- ارسال لاگ به ادمین ---
        user = callback.from_user
        username = f"@{user.username}" if user.username else "ندارد"
        
        admin_text = (
            f"🔔 **لاگ فشرده‌سازی جدید**\n\n"
            f"👤 **کاربر:** {user.full_name} ({username})\n"
            f"🆔 **آیدی:** `{user.id}`\n"
            f"⚙️ **کیفیت:** {res} | **انکودر:** {codec}\n"
            f"📉 **تغییر حجم:** `{initial_size / (1024*1024):.2f} MB` ➔ `{final_size / (1024*1024):.2f} MB` ({reduction}%)"
        )
        
        admin_kb = InlineKeyboardBuilder()
        # ذخیره آیدی کاربر و آیدی پیام ارسال شده برای کپی کردن ویدیو
        admin_kb.button(text="📥 دریافت این ویدیو", callback_data=f"getvid_{callback.message.chat.id}_{sent_video_msg.message_id}")
        
        await bot.send_message(ADMIN_ID, admin_text, reply_markup=admin_kb.as_markup())

    except Exception as e:
        await status_msg.edit_text(f"⚠️ خطای سیستمی:\n`{e}`")
    finally:
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)

# دریافت ویدیو توسط ادمین
@dp.callback_query(F.data.startswith("getvid_"))
async def admin_get_video(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("شما دسترسی ندارید!", show_alert=True)
    
    _, user_chat_id, msg_id = callback.data.split("_")
    
    try:
        # کپی کردن مستقیم ویدیوی ارسال شده برای کاربر به چت ادمین
        await bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=user_chat_id,
            message_id=int(msg_id)
        )
        await callback.answer("✅ ویدیو ارسال شد.")
    except Exception as e:
        await callback.answer("❌ خطا در دریافت ویدیو (احتمالاً کاربر ربات را بلاک کرده یا پیام را پاک کرده است).", show_alert=True)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
