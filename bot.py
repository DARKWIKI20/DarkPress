import os
import re
import time
import asyncio
import logging
import subprocess

import imageio_ffmpeg
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from pyrogram import Client as PyroClient

FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# مقادیر کلیدی
BOT_TOKEN = os.getenv("BOT_TOKEN", "8812733722:AAEFW8oxPPQYyqrqHGtnvS8fTpu3ATxcDbo")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6616272875"))
API_ID = int(os.getenv("API_ID", "12345678"))          # شناسه عددی my.telegram.org را بگذارید
API_HASH = os.getenv("API_HASH", "YOUR_API_HASH_HERE") # هش ۳۲ کاراکتری خود را بگذارید

# افزایش سقف‌ها به ۲ گیگابایت به لطف MTProto
MAX_FILE_SIZE = 2000 * 1024 * 1024 

# کلاینت Aiogram برای پیام‌ها
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# کلاینت Pyrogram برای دور زدن محدودیت‌های دانلود و آپلود
pyro = PyroClient(
    name="bot_engine",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

JOB_QUEUE = asyncio.Queue()
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


def encode_cfg(mode, res, codec, crf, mute, speed):
    m_val = "1" if mute else "0"
    return f"{mode}:{res}:{codec}:{crf}:{m_val}:{speed}"


def decode_cfg(data_str):
    parts = data_str.split(":")
    return {
        "mode": parts[0],
        "res": parts[1],
        "codec": parts[2],
        "crf": parts[3],
        "mute": parts[4] == "1",
        "speed": parts[5]
    }


def build_config_keyboard(cfg: dict):
    b = InlineKeyboardBuilder()
    mode = cfg["mode"]
    res = cfg["res"]
    codec = cfg["codec"]
    crf = cfg["crf"]
    mute = cfg["mute"]
    speed = cfg["speed"]

    b.button(text="🎬 ویدیو" + (" ✅" if mode == "video" else ""), callback_data="cfg:" + encode_cfg("video", res, codec, crf, mute, speed))
    b.button(text="🎵 استخراج MP3" + (" ✅" if mode == "mp3" else ""), callback_data="cfg:" + encode_cfg("mp3", res, codec, crf, mute, speed))
    b.button(text="🎞 گیف GIF" + (" ✅" if mode == "gif" else ""), callback_data="cfg:" + encode_cfg("gif", res, codec, crf, mute, speed))

    if mode == "video":
        for r_k, r_t in [("orig", "ابعاد اصلی"), ("1080", "1080p"), ("720", "720p"), ("480", "480p")]:
            b.button(text=r_t + (" ✅" if res == r_k else ""), callback_data="cfg:" + encode_cfg(mode, r_k, codec, crf, mute, speed))

        b.button(text="H.264" + (" ✅" if codec == "h264" else ""), callback_data="cfg:" + encode_cfg(mode, res, "h264", crf, mute, speed))
        b.button(text="H.265 (کم‌حجم‌تر)" + (" ✅" if codec == "h265" else ""), callback_data="cfg:" + encode_cfg(mode, res, "h265", crf, mute, speed))

        for c_k, c_t in [("light", "کاهش کم"), ("medium", "متعادل"), ("heavy", "کاهش شدید")]:
            b.button(text=c_t + (" ✅" if crf == c_k else ""), callback_data="cfg:" + encode_cfg(mode, res, codec, c_k, mute, speed))

        mute_t = "🔇 صدا: قطع" if mute else "🔊 صدا: وصل"
        b.button(text=mute_t, callback_data="cfg:" + encode_cfg(mode, res, codec, crf, not mute, speed))

    if mode in ["video", "gif"]:
        for s_k, s_t in [("1.0", "1x عادی"), ("1.5", "1.5x"), ("2.0", "2x سریع")]:
            b.button(text=s_t + (" ✅" if speed == s_k else ""), callback_data="cfg:" + encode_cfg(mode, res, codec, crf, mute, s_k))

    cfg_str = encode_cfg(mode, res, codec, crf, mute, speed)
    b.button(text="🚀 ثبت در صف و شروع", callback_data=f"run:{cfg_str}")
    b.button(text="❌ لغو", callback_data="cancel_panel")

    if mode == "video":
        b.adjust(3, 4, 2, 3, 1, 3, 2)
    elif mode == "gif":
        b.adjust(3, 3, 2)
    else:
        b.adjust(3, 2)

    return b.as_markup()


def get_cancel_keyboard(job_id: str):
    b = InlineKeyboardBuilder()
    b.button(text="❌ انصراف / لغو", callback_data=f"stop:{job_id}")
    return b.as_markup()


@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer("🎬 ویدیوی خود را (حداکثر ۲ گیگابایت) ارسال کنید.")


@dp.message(F.video | F.document)
async def handle_video(message: types.Message):
    video = message.video or (
        message.document
        if message.document and message.document.mime_type and message.document.mime_type.startswith("video/")
        else None
    )

    if not video:
        return await message.answer("⚠️ لطفاً فقط فایل ویدیویی ارسال کنید.")

    if video.file_size > MAX_FILE_SIZE:
        return await message.answer(f"❌ حجم فایل ({video.file_size / (1024*1024):.1f} MB) از سقف ۲ گیگابایت بیشتر است.")

    default_cfg = {
        "mode": "video",
        "res": "720",
        "codec": "h264",
        "crf": "medium",
        "mute": False,
        "speed": "1.0"
    }

    res_info = f"\n📏 **ابعاد:** `{video.width}x{video.height}`" if hasattr(video, "width") and video.width else ""

    await message.reply(
        f"⚙️ **تنظیمات پردازش ویدیو:**{res_info}\nتنظیمات را مشخص کرده و روی «شروع» بزنید:",
        reply_markup=build_config_keyboard(default_cfg)
    )


@dp.callback_query(F.data.startswith("cfg:"))
async def update_settings(callback: types.CallbackQuery):
    await callback.answer()
    cfg_data = callback.data[4:]
    cfg = decode_cfg(cfg_data)
    try:
        await callback.message.edit_reply_markup(reply_markup=build_config_keyboard(cfg))
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data == "cancel_panel")
async def cancel_panel(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ عملیات لغو شد.")


@dp.callback_query(F.data.startswith("stop:"))
async def stop_processing(callback: types.CallbackQuery):
    job_id = callback.data.split(":")[1]
    if job_id in ACTIVE_PROCESSES:
        ACTIVE_PROCESSES[job_id]["cancelled"] = True
        proc = ACTIVE_PROCESSES[job_id].get("proc")
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await callback.answer("عملیات متوقف شد.")
        await callback.message.edit_text("🛑 پردازش متوقف شد.")
    else:
        await callback.answer("پردازش در حال حاضر فعال نیست.", show_alert=True)


@dp.callback_query(F.data.startswith("run:"))
async def enqueue_task(callback: types.CallbackQuery):
    await callback.answer()
    cfg_data = callback.data[4:]
    cfg = decode_cfg(cfg_data)

    orig_msg = callback.message.reply_to_message
    if not orig_msg:
        return await callback.message.edit_text("❌ ویدیوی مرجع یافت نشد. لطفاً ویدیو را مجدداً ارسال کنید.")

    video = orig_msg.video or (
        orig_msg.document
        if orig_msg.document and orig_msg.document.mime_type and orig_msg.document.mime_type.startswith("video/")
        else None
    )

    if not video:
        return await callback.message.edit_text("❌ فایل ویدیویی یافت نشد. لطفاً ویدیو را دوباره بفرستید.")

    job_id = f"{callback.message.chat.id}_{callback.message.message_id}"

    status_msg = await callback.message.edit_text(
        f"⏳ در صف انتظار سرور قرار گرفتید...\n👥 نوبت شما: **نفر {JOB_QUEUE.qsize() + 1}**",
        reply_markup=get_cancel_keyboard(job_id)
    )

    job_payload = {
        "job_id": job_id,
        "cfg": cfg,
        "msg_id": orig_msg.message_id,
        "file_size": video.file_size,
        "chat_id": callback.message.chat.id,
        "user": callback.from_user,
        "status_msg": status_msg
    }

    ACTIVE_PROCESSES[job_id] = {"proc": None, "cancelled": False}
    await JOB_QUEUE.put(job_payload)


async def queue_worker():
    while True:
        job = await JOB_QUEUE.get()
        job_id = job["job_id"]

        if ACTIVE_PROCESSES.get(job_id, {}).get("cancelled"):
            JOB_QUEUE.task_done()
            continue

        try:
            await process_job(job)
        except Exception as e:
            logging.error(f"Error on job {job_id}: {e}", exc_info=True)
            try:
                await job["status_msg"].edit_text("⚠️ خطایی در اجرای پردازش پیش آمد.")
            except Exception:
                pass
        finally:
            ACTIVE_PROCESSES.pop(job_id, None)
            JOB_QUEUE.task_done()


async def process_job(job: dict):
    job_id = job["job_id"]
    cfg = job["cfg"]
    status_msg = job["status_msg"]
    mode = cfg["mode"]
    initial_size = job["file_size"]
    speed_factor = float(cfg.get("speed", "1.0"))

    input_path = os.path.join(DOWNLOAD_DIR, f"in_{job_id}.mp4")
    ext = "mp3" if mode == "mp3" else "mp4"
    output_path = os.path.join(DOWNLOAD_DIR, f"out_{job_id}.{ext}")

    # مانیتور دانلود زنده از طریق MTProto
    last_ui_update = 0.0
    async def download_progress(current, total):
        nonlocal last_ui_update
        if time.time() - last_ui_update > 2.5:
            percent = (current / total) * 100.0 if total > 0 else 0.0
            try:
                await status_msg.edit_text(
                    f"📥 در حال دریافت فایل (MTProto):\n{generate_progress_bar(percent)}",
                    reply_markup=get_cancel_keyboard(job_id)
                )
                last_ui_update = time.time()
            except (TelegramBadRequest, TelegramRetryAfter):
                pass

    try:
        await status_msg.edit_text("📥 اتصال به شبکه تلگرام جهت دانلود...", reply_markup=get_cancel_keyboard(job_id))
        
        # دریافت پیام مرجع از طریق Pyrogram جهت دانلود بدون محدودیت ۲۰ مگابایت
        target_pyro_msg = await pyro.get_messages(chat_id=job["chat_id"], message_ids=job["msg_id"])
        await target_pyro_msg.download(
            file_name=input_path,
            progress=download_progress
        )

        if ACTIVE_PROCESSES[job_id]["cancelled"]:
            return

        total_duration = await get_video_duration(input_path)
        effective_duration = total_duration / speed_factor if speed_factor > 0 else total_duration

        await status_msg.edit_text("⚙️ در حال آماده‌سازی و انکود...", reply_markup=get_cancel_keyboard(job_id))

        cmd = [FFMPEG_BIN, "-y", "-i", input_path]

        if mode == "mp3":
            cmd += ["-vn", "-c:a", "libmp3lame", "-b:a", "192k", output_path]

        elif mode == "gif":
            vf_chains = []
            if speed_factor != 1.0:
                vf_chains.append(f"setpts={1.0 / speed_factor}*PTS")
            vf_chains.append("fps=15")
            vf_chains.append("scale=480:-2")

            cmd += [
                "-an",
                "-c:v", "libx264",
                "-vf", ",".join(vf_chains),
                "-crf", "26",
                "-preset", "faster",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-progress", "pipe:2",
                output_path
            ]

        else:
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

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        ACTIVE_PROCESSES[job_id]["proc"] = process

        last_update = 0.0
        time_pattern = re.compile(r"out_time_us=(\d+)")

        while True:
            line = await process.stderr.readline()
            if not line:
                break
            line_str = line.decode(errors="ignore").strip()

            match = time_pattern.search(line_str)
            if match and effective_duration > 0 and not ACTIVE_PROCESSES[job_id]["cancelled"]:
                current_secs = float(match.group(1)) / 1_000_000.0
                percent = min(100.0, (current_secs / effective_duration) * 100.0)

                if time.time() - last_update > 2.5:
                    try:
                        await status_msg.edit_text(
                            f"⚙️ در حال پردازش ({mode}):\n{generate_progress_bar(percent)}",
                            reply_markup=get_cancel_keyboard(job_id)
                        )
                        last_update = time.time()
                    except (TelegramBadRequest, TelegramRetryAfter):
                        pass

        await process.wait()

        if ACTIVE_PROCESSES[job_id]["cancelled"]:
            return

        if process.returncode != 0 or not os.path.exists(output_path):
            return await status_msg.edit_text("❌ پردازش فایل با خطا مواجه شد.")

        final_size = os.path.getsize(output_path)
        reduction = max(0, int(((initial_size - final_size) / initial_size) * 100))

        # مانیتور آپلود زنده
        last_upload_update = 0.0
        async def upload_progress(current, total):
            nonlocal last_upload_update
            if time.time() - last_upload_update > 2.5:
                percent = (current / total) * 100.0 if total > 0 else 0.0
                try:
                    await status_msg.edit_text(f"📤 در حال ارسال به تلگرام:\n{generate_progress_bar(percent)}")
                    last_upload_update = time.time()
                except (TelegramBadRequest, TelegramRetryAfter):
                    pass

        await status_msg.edit_text("📤 در حال ارسال به تلگرام...")

        # ارسال فایل‌های حجیم (تا ۲ گیگابایت) توسط Pyrogram
        if mode == "mp3":
            caption_text = (
                "✅ پردازش با موفقیت انجام شد\n\n"
                f"📦 حجم اولیه: {initial_size / (1024*1024):.2f} MB\n"
                f"📉 حجم نهایی: {final_size / (1024*1024):.2f} MB\n"
                f"⚡ میزان فشرده‌سازی: {reduction}% کاهش (فرمت: MP3)"
            )
            await pyro.send_audio(
                chat_id=job["chat_id"],
                audio=output_path,
                caption=caption_text,
                progress=upload_progress
            )

        elif mode == "gif":
            caption_text = (
                "✅ پردازش با موفقیت انجام شد\n\n"
                f"📦 حجم اولیه: {initial_size / (1024*1024):.2f} MB\n"
                f"📉 حجم نهایی: {final_size / (1024*1024):.2f} MB\n"
                f"⚡ میزان فشرده‌سازی: {reduction}% کاهش (سرعت: {speed_factor}x)"
            )
            await pyro.send_animation(
                chat_id=job["chat_id"],
                animation=output_path,
                caption=caption_text,
                progress=upload_progress
            )

        else:
            caption_text = (
                "✅ پردازش با موفقیت انجام شد\n\n"
                f"📦 حجم اولیه: {initial_size / (1024*1024):.2f} MB\n"
                f"📉 حجم نهایی: {final_size / (1024*1024):.2f} MB\n"
                f"⚡ میزان فشرده‌سازی: {reduction}% کاهش (سرعت: {speed_factor}x)"
            )
            await pyro.send_video(
                chat_id=job["chat_id"],
                video=output_path,
                caption=caption_text,
                supports_streaming=True,
                progress=upload_progress
            )

        await status_msg.delete()

        # ارسال لاگ به ادمین
        if ADMIN_ID and job["user"].id != ADMIN_ID:
            u = job["user"]
            u_name = f"@{u.username}" if u.username else "ندارد"
            admin_text = (
                f"🔔 لاگ پردازش موفق\n\n"
                f"👤 کاربر: {u.full_name} ({u_name}) | {u.id}\n"
                f"🎯 نوع: {mode.upper()}\n"
                f"⚡ تغییر حجم: {initial_size / (1024*1024):.2f} MB ← {final_size / (1024*1024):.2f} MB"
            )
            try:
                await bot.send_message(ADMIN_ID, admin_text)
            except Exception:
                pass

    finally:
        for p in (input_path, output_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    # استارت همزمان کلاینت MTProto پایروگرام
    await pyro.start()
    asyncio.create_task(queue_worker())
    logging.info("ربات MTProto قدرتمند فعال شد (سقف ۲ گیگابایت).")
    try:
        await dp.start_polling(bot)
    finally:
        await pyro.stop()


if __name__ == "__main__":
    asyncio.run(main())
