import os
import re
import sys
import time
import uuid
import asyncio
import logging
import subprocess

# نصب خودکار پیش‌نیازها و باینری FFmpeg
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

FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN", "8812733722:AAEFW8oxPPQYyqrqHGtnvS8fTpu3ATxcDbo")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6616272875"))

MAX_DOWNLOAD_SIZE = 20 * 1024 * 1024
MAX_UPLOAD_SIZE = int(48.5 * 1024 * 1024)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# صف و دیکشنری‌های مدیریت وضعیت
JOB_QUEUE = asyncio.Queue()
TASK_CONFIGS = {}
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
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr.decode(errors="ignore"))
        if match:
            h, m, s = map(float, match.groups())
            return h * 3600 + m * 60 + s
        return 0.0
    except Exception as e:
        logging.warning(f"Error checking duration: {e}")
        return 0.0


def build_config_keyboard(task_id: str, cfg: dict):
    b = InlineKeyboardBuilder()
    mode = cfg.get("mode", "video")

    # انتخاب حالت کاری اصلی
    b.button(text="🎬 ویدیو" + (" ✅" if mode == "video" else ""), callback_data=f"set:{task_id}:mode:video")
    b.button(text="🎵 استخراج MP3" + (" ✅" if mode == "mp3" else ""), callback_data=f"set:{task_id}:mode:mp3")
    b.button(text="🎞 گیف GIF" + (" ✅" if mode == "gif" else ""), callback_data=f"set:{task_id}:mode:gif")

    if mode == "video":
        # رزولوشن
        for r_k, r_t in [("orig", "ابعاد اصلی"), ("1080", "1080p"), ("720", "720p"), ("480", "480p")]:
            b.button(text=r_t + (" ✅" if cfg["res"] == r_k else ""), callback_data=f"set:{task_id}:res:{r_k}")

        # کدک و شدت فشرده‌سازی
        b.button(text="H.264" + (" ✅" if cfg["codec"] == "h264" else ""), callback_data=f"set:{task_id}:codec:h264")
        b.button(text="H.265 (کم‌حجم‌تر)" + (" ✅" if cfg["codec"] == "h265" else ""), callback_data=f"set:{task_id}:codec:h265")

        for c_k, c_t in [("light", "کاهش کم (سریع)"), ("medium", "متعادل"), ("heavy", "کاهش شدید")]:
            b.button(text=c_t + (" ✅" if cfg["crf"] == c_k else ""), callback_data=f"set:{task_id}:crf:{c_k}")

        # کنترل صدا
        mute_t = "🔇 صدا: قطع" if cfg["mute"] else "🔊 صدا: وصل"
        b.button(text=mute_t, callback_data=f"set:{task_id}:mute:{not cfg['mute']}")

    if mode in ["video", "gif"]:
        # کنترل سرعت پخش
        for s_k, s_t in [("1.0", "1x عادی"), ("1.5", "1.5x"), ("2.0", "2x سریع")]:
            b.button(text=s_t + (" ✅" if cfg["speed"] == s_k else ""), callback_data=f"set:{task_id}:speed:{s_k}")

    # کلیدهای اقدام
    b.button(text="🚀 ثبت در صف و شروع", callback_data=f"enqueue:{task_id}")
    b.button(text="❌ لغو", callback_data=f"cancel:{task_id}")

    # چینش استاندارد دکمه‌ها
    if mode == "video":
        b.adjust(3, 4, 2, 3, 1, 3, 2)
    elif mode == "gif":
        b.adjust(3, 3, 2)
    else:
        b.adjust(3, 2)

    return b.as_markup()


def get_cancel_keyboard(task_id: str):
    b = InlineKeyboardBuilder()
    b.button(text="❌ انصراف / لغو", callback_data=f"stop:{task_id}")
    return b.as_markup()


@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer(
        "👋 سلام! ویدیوی خود را (حداکثر ۲۰ مگابایت) ارسال کنید تا امکانات پیشرفته فشرده‌سازی، استخراج صدا و تغییر سرعت نمایش داده شود."
    )


@dp.message(F.video | F.document)
async def handle_video(message: types.Message):
    video = message.video or (
        message.document
        if message.document and message.document.mime_type and message.document.mime_type.startswith("video/")
        else None
    )

    if not video:
        return await message.answer("⚠️ لطفاً فقط فایل ویدیویی بفرستید.")

    if video.file_size > MAX_DOWNLOAD_SIZE:
        return await message.answer(
            f"❌ حجم فایل ({video.file_size / (1024*1024):.1f} MB) از سقف مجاز بات (۲۰ مگابایت) بیشتر است."
        )

    task_id = uuid.uuid4().hex[:8]
    TASK_CONFIGS[task_id] = {
        "file_id": video.file_id,
        "file_size": video.file_size,
        "chat_id": message.chat.id,
        "user": message.from_user,
        "mode": "video",
        "res": "720",
        "codec": "h264",
        "crf": "medium",
        "mute": False,
        "speed": "1.0",
        "msg_id": message.message_id
    }

    res_info = f"\n📏 **ابعاد:** `{video.width}x{video.height}`" if hasattr(video, "width") and video.width else ""

    await message.reply(
        f"⚙️ **پنل تنظیمات پردازش ویدیو:**{res_info}\nگزینه‌های مورد نظر را تنظیم و روی «شروع» کلیک کنید:",
        reply_markup=build_config_keyboard(task_id, TASK_CONFIGS[task_id])
    )


@dp.callback_query(F.data.startswith("set:"))
async def update_settings(callback: types.CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    task_id, key, val = parts[1], parts[2], parts[3]

    if task_id not in TASK_CONFIGS:
        return await callback.message.edit_text("⚠️ این جلسه منقضی شده است.")

    if key == "mute":
        TASK_CONFIGS[task_id]["mute"] = (val.lower() == "true")
    else:
        TASK_CONFIGS[task_id][key] = val

    try:
        await callback.message.edit_reply_markup(
            reply_markup=build_config_keyboard(task_id, TASK_CONFIGS[task_id])
        )
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_panel(callback: types.CallbackQuery):
    task_id = callback.data.split(":")[1]
    TASK_CONFIGS.pop(task_id, None)
    await callback.message.edit_text("❌ عملیات لغو شد.")


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
        await callback.message.edit_text("🛑 پردازش توسط شما لغو شد.")
    else:
        await callback.answer("پردازش در حال حاضر فعال نیست.", show_alert=True)


@dp.callback_query(F.data.startswith("enqueue:"))
async def enqueue_task(callback: types.CallbackQuery):
    await callback.answer()
    task_id = callback.data.split(":")[1]

    if task_id not in TASK_CONFIGS:
        return await callback.message.edit_text("❌ این نشست منقضی شده است.")

    cfg = TASK_CONFIGS[task_id]
    status_msg = await callback.message.edit_text(
        f"⏳ در صف انتظار سرور قرار گرفتید...\n👥 نوبت شما: **نفر {JOB_QUEUE.qsize() + 1}**",
        reply_markup=get_cancel_keyboard(task_id)
    )

    ACTIVE_PROCESSES[task_id] = {"proc": None, "cancelled": False, "status_msg": status_msg}
    await JOB_QUEUE.put((task_id, cfg, status_msg))


# موتور پردازش نوبتی (Worker) جهت جلوگیری از اشباع CPU/RAM
async def queue_worker():
    while True:
        task_id, cfg, status_msg = await JOB_QUEUE.get()

        if ACTIVE_PROCESSES.get(task_id, {}).get("cancelled"):
            JOB_QUEUE.task_done()
            continue

        try:
            await process_job(task_id, cfg, status_msg)
        except Exception as e:
            logging.error(f"Worker Error on task {task_id}: {e}", exc_info=True)
            try:
                await status_msg.edit_text("⚠️ متأسفانه در اجرای عملیات خطایی رخ داد.")
            except Exception:
                pass
        finally:
            ACTIVE_PROCESSES.pop(task_id, None)
            TASK_CONFIGS.pop(task_id, None)
            JOB_QUEUE.task_done()


async def process_job(task_id: str, cfg: dict, status_msg: types.Message):
    mode = cfg["mode"]
    file_id = cfg["file_id"]
    initial_size = cfg["file_size"]
    user = cfg["user"]

    input_path = os.path.join(DOWNLOAD_DIR, f"in_{task_id}.mp4")
    ext = "mp3" if mode == "mp3" else ("gif" if mode == "gif" else "mp4")
    output_path = os.path.join(DOWNLOAD_DIR, f"out_{task_id}.{ext}")

    try:
        await status_msg.edit_text("📥 در حال دریافت فایل...", reply_markup=get_cancel_keyboard(task_id))
        file_info = await bot.get_file(file_id)
        await bot.download_file(file_info.file_path, destination=input_path)

        if ACTIVE_PROCESSES[task_id]["cancelled"]:
            return

        total_duration = await get_video_duration(input_path)
        speed_factor = float(cfg.get("speed", "1.0"))
        effective_duration = total_duration / speed_factor if speed_factor > 0 else total_duration

        await status_msg.edit_text("⚙️ در حال آماده‌سازی پردازش...", reply_markup=get_cancel_keyboard(task_id))

        # ساخت خط فرمان FFmpeg بر اساس تنظیمات
        cmd = [FFMPEG_BIN, "-y", "-i", input_path]

        if mode == "mp3":
            cmd += ["-vn", "-c:a", "libmp3lame", "-q:a", "2", output_path]

        elif mode == "gif":
            fps = "15"
            speed_filter = f"setpts={1.0 / speed_factor}*PTS," if speed_factor != 1.0 else ""
            vf = f"{speed_filter}fps={fps},scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
            cmd += ["-vf", vf, output_path]

        else:  # mode == video
            crf_map = {"light": "23", "medium": "28", "heavy": "34"}
            v_codec = "libx265" if cfg["codec"] == "h265" else "libx264"
            scale_filter = "scale=trunc(iw/2)*2:trunc(ih/2)*2" if cfg["res"] == "orig" else f"scale=-2:{cfg['res']}"

            vf_chains = []
            if speed_factor != 1.0:
                vf_chains.append(f"setpts={1.0 / speed_factor}*PTS")
            vf_chains.append(scale_filter)

            cmd += [
                "-map", "0:v:0",
                "-c:v", v_codec,
                "-vf", ",".join(vf_chains),
                "-crf", crf_map.get(cfg["crf"], "28"),
                "-preset", "faster",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart"
            ]

            if cfg["mute"]:
                cmd += ["-an"]
            else:
                cmd += ["-map", "0:a:0?"]
                if speed_factor != 1.0:
                    cmd += ["-filter:a", f"atempo={speed_factor}"]
                cmd += ["-c:a", "aac", "-b:a", "128k"]

            cmd += ["-progress", "pipe:2", output_path]

        # اجرای پردازش
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
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
            if match and effective_duration > 0 and not ACTIVE_PROCESSES[task_id]["cancelled"]:
                current_secs = float(match.group(1)) / 1_000_000.0
                percent = min(100.0, (current_secs / effective_duration) * 100.0)

                if time.time() - last_update > 2.5:
                    try:
                        await status_msg.edit_text(
                            f"⚙️ در حال تبدیل ({mode}):\n{generate_progress_bar(percent)}",
                            reply_markup=get_cancel_keyboard(task_id)
                        )
                        last_update = time.time()
                    except (TelegramBadRequest, TelegramRetryAfter):
                        pass

        await process.wait()

        if ACTIVE_PROCESSES[task_id]["cancelled"]:
            return

        if process.returncode != 0 or not os.path.exists(output_path):
            return await status_msg.edit_text("❌ پردازش فایل با خطا مواجه شد.")

        final_size = os.path.getsize(output_path)
        if final_size > MAX_UPLOAD_SIZE:
            return await status_msg.edit_text("❌ حجم خروجی از حد مجاز آپلود تلگرام بیشتر شد.")

        await status_msg.edit_text("📤 در حال آپلود خروجی...")

        # ارسال فایل متناسب با فرمت انتخابی
        if mode == "mp3":
            await bot.send_audio(
                chat_id=cfg["chat_id"],
                audio=FSInputFile(output_path, filename=f"audio_{task_id}.mp3"),
                caption=f"🎵 **استخراج صدا کامل شد**\nحجم: `{final_size / (1024*1024):.2f} MB`"
            )
        elif mode == "gif":
            await bot.send_animation(
                chat_id=cfg["chat_id"],
                animation=FSInputFile(output_path, filename=f"anim_{task_id}.gif"),
                caption=f"🎞 **گیف با موفقیت ساخته شد**\nحجم: `{final_size / (1024*1024):.2f} MB`"
            )
        else:
            reduction = max(0, int(((initial_size - final_size) / initial_size) * 100))
            await bot.send_video(
                chat_id=cfg["chat_id"],
                video=FSInputFile(output_path, filename=f"video_{task_id}.mp4"),
                caption=(
                    f"✅ **پردازش ویدیو کامل شد**\n"
                    f"حجم اولیه: `{initial_size / (1024*1024):.2f} MB`\n"
                    f"حجم نهایی: `{final_size / (1024*1024):.2f} MB`\n"
                    f"کاهش حجم: `{reduction}%` | سرعت: `{speed_factor}x`"
                ),
                supports_streaming=True
            )

        await status_msg.delete()

    finally:
        for p in (input_path, output_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    # اجرای Worker صف به شکل پس‌زمینه
    asyncio.create_task(queue_worker())
    logging.info("ربات و Worker صف در Railway آماده به کار هستند.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
