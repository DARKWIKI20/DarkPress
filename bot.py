import os
import re
import sys
import time
import uuid
import asyncio
import logging
import subprocess

# نصب و تأمین خودکار پکیج‌ها و باینری FFmpeg
def setup_dependencies():
    packages = ["aiogram", "imageio-ffmpeg"]
    for pkg in packages:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

setup_dependencies()

import imageio_ffmpeg
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import FSInputFile

# آدرس فایل باینری کامپایل‌شده FFmpeg
FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN", "8812733722:AAEFW8oxPPQYyqrqHGtnvS8fTpu3ATxcDbo")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6616272875"))

MAX_DOWNLOAD_SIZE = 20 * 1024 * 1024
MAX_UPLOAD_SIZE = int(48.5 * 1024 * 1024)

MAX_CONCURRENT_TASKS = 2
task_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

TASK_STORAGE = {}
ACTIVE_PROCESSES = {}


def generate_progress_bar(percent: float) -> str:
    total_blocks = 15
    filled = int(round((percent / 100) * total_blocks))
    filled = min(total_blocks, max(0, filled))
    bar = "█" * filled + "░" * (total_blocks - filled)
    return f"[{bar}] {percent:.1f}%"


async def get_video_duration(file_path: str) -> float:
    cmd = [FFMPEG_BIN, "-i", file_path]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr.decode(errors="ignore"))
        if match:
            h, m, s = map(float, match.groups())
            return h * 3600 + m * 60 + s
        return 0.0
    except Exception as e:
        logging.warning(f"Failed to detect video duration: {e}")
        return 0.0


def get_config_keyboard(task_id: str, res: str, codec: str):
    builder = InlineKeyboardBuilder()

    resolutions = [("orig", "اصلی"), ("1080", "1080p"), ("720", "720p"), ("480", "480p")]
    for r_key, r_label in resolutions:
        label = f"{r_label} ✅" if res == r_key else r_label
        builder.button(text=label, callback_data=f"cfg:{task_id}:{r_key}:{codec}")

    codecs = [("h264", "H.264"), ("h265", "H.265")]
    for c_key, c_label in codecs:
        label = f"{c_label} ✅" if codec == c_key else c_label
        builder.button(text=label, callback_data=f"cfg:{task_id}:{res}:{c_key}")

    builder.button(text="🚀 شروع", callback_data=f"run:{task_id}:{res}:{codec}")
    builder.button(text="❌ لغو", callback_data=f"cancel:{task_id}")

    builder.adjust(4, 2, 2)
    return builder.as_markup()


def get_cancel_keyboard(task_id: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ لغو پردازش", callback_data=f"stop:{task_id}")
    return builder.as_markup()


@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer("🎬 ویدیوی خود را (حداکثر ۲۰ مگابایت) ارسال کنید.")


@dp.message(F.video | F.document)
async def handle_video(message: types.Message):
    video = message.video or (
        message.document
        if message.document and message.document.mime_type and message.document.mime_type.startswith("video/")
        else None
    )

    if not video:
        return await message.answer("⚠️ لطفاً فقط فایل ویدیویی ارسال کنید.")

    if video.file_size > MAX_DOWNLOAD_SIZE:
        return await message.answer(
            f"❌ حجم فایل ({video.file_size / (1024*1024):.1f} MB) از سقف مجاز بات (۲۰ مگابایت) بیشتر است."
        )

    task_id = uuid.uuid4().hex[:8]
    TASK_STORAGE[task_id] = {
        "file_id": video.file_id,
        "file_size": video.file_size,
        "chat_id": message.chat.id,
        "user": message.from_user
    }

    res_info = f"\n\n📏 **ابعاد:** `{video.width}x{video.height}`" if hasattr(video, "width") and video.width else ""

    await message.reply(
        f"⚙️ **تنظیمات خروجی را انتخاب کنید:**{res_info}",
        reply_markup=get_config_keyboard(task_id, "720", "h264")
    )


@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_panel(callback: types.CallbackQuery):
    task_id = callback.data.split(":")[1]
    TASK_STORAGE.pop(task_id, None)
    await callback.message.edit_text("❌ عملیات لغو شد.")


@dp.callback_query(F.data.startswith("cfg:"))
async def update_config(callback: types.CallbackQuery):
    await callback.answer()
    _, task_id, res, codec = callback.data.split(":")

    try:
        await callback.message.edit_reply_markup(
            reply_markup=get_config_keyboard(task_id, res, codec)
        )
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data.startswith("stop:"))
async def stop_processing(callback: types.CallbackQuery):
    task_id = callback.data.split(":")[1]
    if task_id in ACTIVE_PROCESSES:
        ACTIVE_PROCESSES[task_id]["cancelled"] = True
        proc = ACTIVE_PROCESSES[task_id].get("proc")
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await callback.answer("عملیات متوقف شد.")
        await callback.message.edit_text("🛑 پردازش توسط شما متوقف شد.")
    else:
        await callback.answer("پردازش قبلاً لغو یا تمام شده است.", show_alert=True)


@dp.callback_query(F.data.startswith("run:"))
async def start_process(callback: types.CallbackQuery):
    await callback.answer()
    _, task_id, res, codec = callback.data.split(":")

    file_id = None
    initial_size = 0
    user = callback.from_user

    # در صورت ریستارت کانتینر، دیتا از پیام اصلی بازیابی می‌شود
    if task_id in TASK_STORAGE:
        task_data = TASK_STORAGE.pop(task_id)
        file_id = task_data["file_id"]
        initial_size = task_data["file_size"]
        user = task_data["user"]
    elif callback.message.reply_to_message:
        orig = callback.message.reply_to_message
        target = orig.video or (orig.document if orig.document and orig.document.mime_type and orig.document.mime_type.startswith("video/") else None)
        if target:
            file_id = target.file_id
            initial_size = target.file_size

    if not file_id:
        return await callback.message.edit_text("❌ فایل پیدا نشد. لطفاً مجدداً ویدیو را بفرستید.")

    status_msg = await callback.message.edit_text(
        "⏳ در صف انتظار پردازش سرور...",
        reply_markup=get_cancel_keyboard(task_id)
    )

    input_path = os.path.join(DOWNLOAD_DIR, f"in_{task_id}.mp4")
    output_path = os.path.join(DOWNLOAD_DIR, f"out_{task_id}.mp4")
    re_output_path = os.path.join(DOWNLOAD_DIR, f"safe_{task_id}.mp4")

    ACTIVE_PROCESSES[task_id] = {"proc": None, "cancelled": False}

    async with task_semaphore:
        if ACTIVE_PROCESSES[task_id]["cancelled"]:
            return

        try:
            await status_msg.edit_text("📥 در حال دانلود از تلگرام...", reply_markup=get_cancel_keyboard(task_id))
            file_info = await bot.get_file(file_id)
            await bot.download_file(file_info.file_path, destination=input_path)

            if ACTIVE_PROCESSES[task_id]["cancelled"]:
                return

            total_duration = await get_video_duration(input_path)
            await status_msg.edit_text("🔧 آماده‌سازی اینکودر...", reply_markup=get_cancel_keyboard(task_id))

            v_codec = "libx265" if codec == "h265" else "libx264"
            scale_filter = "scale=trunc(iw/2)*2:trunc(ih/2)*2" if res == "orig" else f"scale=-2:{res}"

            cmd = [
                FFMPEG_BIN, "-y", "-i", input_path,
                "-map", "0:v:0", "-map", "0:a:0?",
                "-c:v", v_codec,
                "-vf", scale_filter,
                "-crf", "28",
                "-preset", "faster",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-c:a", "aac", "-b:a", "128k",
                "-progress", "pipe:2",
                output_path
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )
            ACTIVE_PROCESSES[task_id]["proc"] = process

            last_update = 0.0
            time_pattern = re.compile(r"out_time_us=(\d+)")

            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                line_str = line.decode(errors="ignore").strip()

                match = time_pattern.search(line_str)
                if match and total_duration > 0 and not ACTIVE_PROCESSES[task_id]["cancelled"]:
                    current_seconds = float(match.group(1)) / 1_000_000.0
                    percent = min(100.0, (current_seconds / total_duration) * 100.0)

                    if time.time() - last_update > 2.5:
                        try:
                            await status_msg.edit_text(
                                f"⚙️ فشرده‌سازی ویدیو:\n{generate_progress_bar(percent)}",
                                reply_markup=get_cancel_keyboard(task_id)
                            )
                            last_update = time.time()
                        except (TelegramBadRequest, TelegramRetryAfter):
                            pass

            await process.wait()

            if ACTIVE_PROCESSES[task_id]["cancelled"]:
                return

            if process.returncode != 0 or not os.path.exists(output_path):
                return await status_msg.edit_text("❌ خطا در فرآیند تبدیل فایل.")

            final_size = os.path.getsize(output_path)

            if final_size > MAX_UPLOAD_SIZE and total_duration > 0:
                await status_msg.edit_text("⚠️ تنظیم مجدد بیت‌ریت جهت تطابق با سقف تلگرام...")
                target_total_bits = 45 * 8 * 1024 * 1024
                target_bitrate = max(100, int((target_total_bits / total_duration) / 1000) - 96)

                re_cmd = [
                    FFMPEG_BIN, "-y", "-i", output_path,
                    "-c:v", v_codec,
                    "-b:v", f"{target_bitrate}k",
                    "-maxrate", f"{int(target_bitrate * 1.2)}k",
                    "-bufsize", f"{target_bitrate * 2}k",
                    "-preset", "faster",
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                    "-c:a", "aac", "-b:a", "96k",
                    re_output_path
                ]
                re_proc = await asyncio.create_subprocess_exec(
                    *re_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                await re_proc.wait()

                if os.path.exists(re_output_path) and os.path.getsize(re_output_path) > 0:
                    os.replace(re_output_path, output_path)
                    final_size = os.path.getsize(output_path)

            if final_size > MAX_UPLOAD_SIZE:
                return await status_msg.edit_text("❌ حجم نهایی ویدیو بیش از ۴۸.۵ مگابایت شد و تلگرام اجازه آپلود آن را نمی‌دهد.")

            reduction = max(0, int(((initial_size - final_size) / initial_size) * 100))
            await status_msg.edit_text("📤 در حال آپلود...")

            sent_video = await bot.send_video(
                chat_id=callback.message.chat.id,
                video=FSInputFile(output_path, filename=f"compressed_{task_id}.mp4"),
                caption=(
                    f"✅ **عملیات انجام شد**\n"
                    f"حجم اولیه: `{initial_size / (1024*1024):.2f} MB`\n"
                    f"حجم جدید: `{final_size / (1024*1024):.2f} MB`\n"
                    f"کاهش حجم: `{reduction}%`"
                ),
                supports_streaming=True
            )
            await status_msg.delete()

            if ADMIN_ID and user.id != ADMIN_ID:
                username = f"@{user.username}" if user.username else "ندارد"
                admin_text = (
                    f"🔔 **گزارش تبدیل ویدیو**\n\n"
                    f"👤 کاربر: {user.full_name} ({username}) | `{user.id}`\n"
                    f"⚙️ کیفیت: {res} | کدک: {codec}\n"
                    f"📉 نتیجه: `{initial_size / (1024*1024):.2f} MB` ➔ `{final_size / (1024*1024):.2f} MB` ({reduction}%)"
                )
                admin_kb = InlineKeyboardBuilder()
                admin_kb.button(
                    text="📥 فوروارد ویدیو",
                    callback_data=f"getvid:{callback.message.chat.id}:{sent_video.message_id}"
                )
                try:
                    await bot.send_message(ADMIN_ID, admin_text, reply_markup=admin_kb.as_markup())
                except Exception as ex:
                    logging.warning(f"Error sending log to admin: {ex}")

        except Exception as e:
            logging.error(f"Error during video processing: {e}", exc_info=True)
            if not ACTIVE_PROCESSES.get(task_id, {}).get("cancelled"):
                await status_msg.edit_text(f"⚠️ خطای غیرمنتظره:\n`{e}`")
        finally:
            ACTIVE_PROCESSES.pop(task_id, None)
            for path in (input_path, output_path, re_output_path):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass


@dp.callback_query(F.data.startswith("getvid:"))
async def admin_get_video(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("دسترسی غیرمجاز است.", show_alert=True)

    _, chat_id, msg_id = callback.data.split(":")
    try:
        await bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=int(chat_id),
            message_id=int(msg_id)
        )
        await callback.answer("✅ ویدیو فوروارد شد.")
    except Exception:
        await callback.answer("❌ پیام در چت کاربر حذف شده است.", show_alert=True)


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("ربات در Railway با موفقیت راه‌اندازی شد.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
