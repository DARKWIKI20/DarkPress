import os
import subprocess
import uuid
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask

app = FastAPI(title="Pro Video Compressor")
UPLOAD_DIR = "/tmp/videos"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def cleanup_files(*file_paths):
    for path in file_paths:
        if os.path.exists(path):
            try: os.remove(path)
            except: pass

@app.get("/", response_class=HTMLResponse)
async def index():
    return """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>فشرده‌ساز حرفه‌ای ویدیو</title>
    <style>
        body {
            font-family: system-ui, sans-serif;
            background: linear-gradient(135deg, #0f172a, #1e1b4b);
            color: #e2e8f0; display: flex; align-items: center; justify-content: center;
            min-height: 100vh; margin: 0; position: relative; overflow: hidden;
        }
        .bg-blob { position: absolute; width: 400px; height: 400px; background: #6366f1; filter: blur(80px); border-radius: 50%; opacity: 0.4; top: -10%; left: -10%; }
        .bg-blob2 { position: absolute; width: 300px; height: 300px; background: #ec4899; filter: blur(80px); border-radius: 50%; opacity: 0.3; bottom: -10%; right: -10%; }
        .glass-card {
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 4px 30px rgba(0, 0, 0, 0.1);
            border-radius: 20px;
            padding: 2.5rem;
            width: 100%; max-width: 480px;
            position: relative; z-index: 10;
            text-align: center;
        }
        h2 { margin-bottom: 2rem; color: #fff; font-weight: 600; }
        .input-group { margin-bottom: 1.5rem; text-align: right; }
        label { display: block; margin-bottom: 0.5rem; font-size: 0.9rem; color: #cbd5e1; }
        select, input[type="file"] {
            width: 100%; padding: 0.75rem;
            background: rgba(0,0,0,0.2); border: 1px solid rgba(255,255,255,0.1);
            border-radius: 10px; color: #fff; outline: none;
            box-sizing: border-box; transition: all 0.3s ease;
        }
        select:focus, input[type="file"]:focus { border-color: #818cf8; background: rgba(0,0,0,0.4); }
        option { background: #1e293b; color: white; }
        button {
            background: linear-gradient(135deg, #6366f1, #a855f7);
            color: white; border: none; padding: 1rem;
            border-radius: 10px; font-size: 1.1rem; font-weight: bold;
            cursor: pointer; width: 100%; transition: transform 0.2s, box-shadow 0.2s;
        }
        button:hover { transform: translateY(-2px); box-shadow: 0 10px 20px rgba(99, 102, 241, 0.4); }
        button:disabled { opacity: 0.5; cursor: not-allowed; transform: none; box-shadow: none; }
        #status { margin-top: 1.5rem; font-size: 0.95rem; color: #a7f3d0; min-height: 20px; }
    </style>
</head>
<body>
    <div class="bg-blob"></div>
    <div class="bg-blob2"></div>
    <div class="glass-card">
        <h2>فشرده‌ساز حرفه‌ای</h2>
        <form id="uploadForm">
            <div class="input-group">
                <label>فایل ویدیویی (هر فرمتی)</label>
                <input type="file" id="videoFile" accept="video/*, .mkv, .avi, .mov, .flv" required>
            </div>
            <div class="input-group">
                <label>نوع انکودر (Codec)</label>
                <select id="codec">
                    <option value="libx264">H.264 (سازگاری کامل)</option>
                    <option value="libx265">H.265 (نهایت فشرده‌سازی)</option>
                </select>
            </div>
            <div class="input-group">
                <label>کیفیت و ابعاد نهایی</label>
                <select id="quality">
                    <option value="orig">ابعاد اصلی (Original)</option>
                    <option value="720">کیفیت 720p</option>
                    <option value="480">کیفیت 480p</option>
                </select>
            </div>
            <button type="submit" id="submitBtn">شروع پردازش</button>
        </form>
        <div id="status"></div>
    </div>
    <script>
        const form = document.getElementById('uploadForm');
        const statusDiv = document.getElementById('status');
        const submitBtn = document.getElementById('submitBtn');

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const fileInput = document.getElementById('videoFile').files[0];
            if (!fileInput) return;

            submitBtn.disabled = true;
            statusDiv.innerHTML = '⚙️ در حال آپلود و پردازش... <br><small>بسته به حجم فایل ممکن است دقایقی طول بکشد.</small>';

            const formData = new FormData();
            formData.append('file', fileInput);
            formData.append('codec', document.getElementById('codec').value);
            formData.append('quality', document.getElementById('quality').value);

            try {
                const response = await fetch('/compress', { method: 'POST', body: formData });
                if (!response.ok) throw new Error('خطا در سمت سرور');

                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'compressed_' + fileInput.name;
                document.body.appendChild(a);
                a.click();
                a.remove();
                statusDiv.innerText = '✅ پردازش تمام شد و فایل آماده دانلود است!';
            } catch (err) {
                statusDiv.innerText = '❌ مشکلی رخ داد، دوباره تلاش کنید.';
            } finally {
                submitBtn.disabled = false;
            }
        });
    </script>
</body>
</html>"""

@app.post("/compress")
async def compress_video(
    file: UploadFile = File(...),
    codec: str = Form(...),
    quality: str = Form(...)
):
    task_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1] or ".mp4"
    input_path = os.path.join(UPLOAD_DIR, f"{task_id}_in{ext}")
    output_path = os.path.join(UPLOAD_DIR, f"{task_id}_out.mp4")

    try:
        with open(input_path, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                buffer.write(chunk)
    except:
        cleanup_files(input_path)
        raise HTTPException(status_code=500, detail="Upload error")

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vcodec", codec, "-crf", "28", "-preset", "veryfast", "-threads", "1",
        "-acodec", "aac", "-b:a", "128k"
    ]
    if quality == "720": cmd.extend(["-vf", "scale=-2:720"])
    elif quality == "480": cmd.extend(["-vf", "scale=-2:480"])
    cmd.append(output_path)

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        cleanup_files(input_path, output_path)
        raise HTTPException(status_code=500, detail="Compression error")

    return FileResponse(
        output_path, media_type="video/mp4",
        filename=f"compressed_{os.path.splitext(file.filename)[0]}.mp4",
        background=BackgroundTask(cleanup_files, input_path, output_path)
    )
