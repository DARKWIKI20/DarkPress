import os
import subprocess
import uuid
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask

app = FastAPI(title="Video Compressor")

UPLOAD_DIR = "/tmp/videos"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def cleanup_files(*file_paths):
    for path in file_paths:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

@app.get("/", response_class=HTMLResponse)
async def index():
    # صفحه وب شیک برای تست و آپلود ویدیو با مرورگر
    return """
    <!DOCTYPE html>
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>فشرده‌ساز ویدیو</title>
        <style>
            body {
                font-family: system-ui, -apple-system, sans-serif;
                background: #0f172a;
                color: #e2e8f0;
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                margin: 0;
            }
            .card {
                background: #1e293b;
                padding: 2rem;
                border-radius: 1rem;
                box-shadow: 0 10px 25px rgba(0,0,0,0.5);
                width: 100%;
                max-width: 450px;
                text-align: center;
                border: 1px solid #334155;
            }
            h2 { margin-bottom: 1.5rem; color: #38bdf8; }
            input[type="file"] {
                display: block;
                width: 100%;
                margin: 1.5rem 0;
                padding: 0.75rem;
                background: #0f172a;
                border: 1px dashed #475569;
                border-radius: 0.5rem;
                color: #94a3b8;
                box-sizing: border-box;
            }
            button {
                background: #0284c7;
                color: white;
                border: none;
                padding: 0.75rem 1.5rem;
                border-radius: 0.5rem;
                font-size: 1rem;
                cursor: pointer;
                width: 100%;
                font-weight: bold;
                transition: background 0.2s;
            }
            button:hover { background: #0369a1; }
            #status { margin-top: 1rem; font-size: 0.9rem; color: #94a3b8; }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>سرویس کاهش حجم ویدیو</h2>
            <form id="uploadForm">
                <input type="file" id="videoFile" accept="video/*" required>
                <button type="submit" id="submitBtn">شروع فشرده‌سازی و دانلود</button>
            </form>
            <div id="status"></div>
        </div>

        <script>
            const form = document.getElementById('uploadForm');
            const statusDiv = document.getElementById('status');
            const submitBtn = document.getElementById('submitBtn');

            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                const fileInput = document.getElementById('videoFile');
                if (!fileInput.files[0]) return;

                submitBtn.disabled = true;
                statusDiv.innerText = 'در حال آپلود و فشرده‌سازی... لطفاً پنجره را نبندید.';

                const formData = new FormData();
                formData.append('file', fileInput.files[0]);

                try {
                    const response = await fetch('/compress', {
                        method: 'POST',
                        body: formData
                    });

                    if (!response.ok) throw new Error('خطا در فشرده‌سازی');

                    const blob = await response.blob();
                    const downloadUrl = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = downloadUrl;
                    a.download = 'compressed_' + fileInput.files[0].name;
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    statusDiv.innerText = 'عملیات با موفقیت انجام شد و فایل دانلود شد!';
                } catch (err) {
                    statusDiv.innerText = 'مشکلی رخ داد، دوباره تلاش کنید.';
                } finally {
                    submitBtn.disabled = false;
                }
            });
        </script>
    </body>
    </html>
    """

@app.post("/compress")
async def compress_video(file: UploadFile = File(...)):
    task_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1] or ".mp4"
    input_path = os.path.join(UPLOAD_DIR, f"{task_id}_in{ext}")
    output_path = os.path.join(UPLOAD_DIR, f"{task_id}_out.mp4")

    # ذخیره فایل به صورت تکه‌ای روی هارد تا RAM اشغال نشود
    try:
        with open(input_path, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                buffer.write(chunk)
    except Exception:
        cleanup_files(input_path)
        raise HTTPException(status_code=500, detail="Error uploading file")

    # اجرای بهینه‌سازی‌شده FFmpeg
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vcodec", "libx264",
        "-crf", "28",
        "-preset", "veryfast",
        "-threads", "1",
        "-acodec", "aac",
        "-b:a", "128k",
        output_path
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if result.returncode != 0:
        cleanup_files(input_path, output_path)
        raise HTTPException(status_code=500, detail="Error compressing video")

    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=f"compressed_{os.path.splitext(file.filename)[0]}.mp4",
        background=BackgroundTask(cleanup_files, input_path, output_path)
    )
