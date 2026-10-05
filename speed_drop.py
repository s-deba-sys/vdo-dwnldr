import mimetypes
import os
from pathlib import Path
import socket
import urllib.parse
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
import qrcode
import uvicorn

PORT = 8000
BASE_DIR = Path("./transfer_box").resolve()
BASE_DIR.mkdir(parents=True, exist_ok=True)

CHUNK_SIZE = 4 * 1024 * 1024  # 4MB buffer for maximum local LAN throughput

app = FastAPI(title="SpeedDrop")


def get_local_ip():
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    s.connect(("10.255.255.255", 1))
    ip = s.getsockname()[0]
  except Exception:
    ip = "127.0.0.1"
  finally:
    s.close()
  return ip


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>SpeedDrop Local Transfer</title>
<style>
  :root { 
    --bg: #0b0f19;
    --card: #151e2e;
    --border: #223049;
    --primary: #3b82f6;
    --primary-hover: #2563eb;
    --text: #f1f5f9;
    --muted: #94a3b8;
    --success: #10b981;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
  }
  * { box-sizing: border-box; }
  body { 
    background: var(--bg); 
    color: var(--text); 
    max-width: 680px; 
    margin: 0 auto; 
    padding: 28px 16px 60px 16px; 
    -webkit-font-smoothing: antialiased;
  }
  header { margin-bottom: 24px; }
  h1 { font-size: 1.6rem; margin: 0 0 4px 0; font-weight: 700; letter-spacing: -0.02em; }
  p.sub { color: var(--muted); font-size: 0.9rem; margin: 0; }
  .card { 
    background: var(--card); 
    border: 1px solid var(--border); 
    border-radius: 14px; 
    padding: 20px; 
    margin-bottom: 24px; 
    box-shadow: 0 8px 24px rgba(0,0,0,0.35); 
  }
  .drop-zone {
    border: 2px dashed #334155;
    border-radius: 10px;
    padding: 28px 16px;
    text-align: center;
    cursor: pointer;
    transition: all 0.2s ease;
    background: rgba(15, 23, 42, 0.4);
  }
  .drop-zone.dragover {
    border-color: var(--primary);
    background: rgba(59, 130, 246, 0.08);
  }
  .btn-upload {
    display: inline-block;
    background: var(--primary);
    color: #fff;
    padding: 12px 24px;
    border-radius: 8px;
    font-size: 0.95rem;
    font-weight: 600;
    margin-top: 10px;
    box-shadow: 0 4px 12px rgba(59, 130, 246, 0.25);
  }
  .progress-section { 
    display: none; 
    margin-top: 18px; 
  }
  .progress-wrap { 
    background: #1e293b; 
    border-radius: 999px; 
    height: 12px; 
    overflow: hidden; 
  }
  .progress-bar { 
    width: 0%; 
    height: 100%; 
    background: var(--success); 
    transition: width 0.12s linear; 
  }
  .speed-text { 
    font-size: 0.84rem; 
    color: #38bdf8; 
    text-align: center; 
    margin-top: 10px; 
    font-variant-numeric: tabular-nums; 
  }
  h2 { font-size: 1.1rem; margin: 0 0 14px 0; color: #cbd5e1; font-weight: 600; }
  ul { list-style: none; padding: 0; margin: 0; }
  li { 
    display: flex; 
    align-items: center; 
    justify-content: space-between; 
    padding: 12px 0; 
    border-bottom: 1px solid #1e293b; 
  }
  li:last-child { border-bottom: none; }
  .file-info { min-width: 0; flex: 1; margin-right: 14px; }
  .file-name { 
    font-size: 0.92rem; 
    font-weight: 500; 
    white-space: nowrap; 
    overflow: hidden; 
    text-overflow: ellipsis; 
    display: block; 
  }
  .file-size { color: var(--muted); font-size: 0.78rem; margin-top: 2px; display: block; }
  .dl-btn { 
    background: #1e293b; 
    color: #38bdf8; 
    border: 1px solid #334155; 
    padding: 8px 14px; 
    border-radius: 7px; 
    text-decoration: none; 
    font-size: 0.82rem; 
    font-weight: 600; 
    transition: background 0.15s; 
  }
  .dl-btn:hover { background: #334155; }
</style>
</head>
<body>
  <header>
    <h1>⚡ SpeedDrop</h1>
    <p class="sub">Direct LAN peer-to-peer file bridge (PC & iPhone)</p>
  </header>

  <div class="card">
    <div class="drop-zone" id="dropZone">
      <div style="font-size: 1.8rem; margin-bottom: 6px;">📂</div>
      <div style="font-size: 0.95rem; font-weight: 500;">Tap to select or drop files here</div>
      <div style="color: var(--muted); font-size: 0.8rem; margin-top: 4px;">Supports videos, photos, documents & ZIPs</div>
      <input type="file" id="filePicker" multiple style="display: none;">
    </div>

    <div class="progress-section" id="progressSec">
      <div class="progress-wrap">
        <div class="progress-bar" id="progressBar"></div>
      </div>
      <div class="speed-text" id="speedText">Initializing...</div>
    </div>
  </div>

  <div class="card">
    <h2>Available Files on Storage</h2>
    <ul id="fileList"><li style="color: var(--muted);">Loading storage index...</li></ul>
  </div>

<script>
  const picker = document.getElementById('filePicker');
  const dropZone = document.getElementById('dropZone');
  const progSec = document.getElementById('progressSec');
  const pBar = document.getElementById('progressBar');
  const sText = document.getElementById('speedText');

  dropZone.addEventListener('click', () => picker.click());

  ['dragenter', 'dragover'].forEach(name => {
    dropZone.addEventListener(name, (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
  });
  ['dragleave', 'drop'].forEach(name => {
    dropZone.addEventListener(name, (e) => { e.preventDefault(); dropZone.classList.remove('dragover'); });
  });

  dropZone.addEventListener('drop', (e) => {
    if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
  });

  picker.addEventListener('change', (e) => {
    if (e.target.files.length) handleFiles(e.target.files);
  });

  async function handleFiles(files) {
    progSec.style.display = 'block';
    for (let i = 0; i < files.length; i++) {
      await uploadFile(files[i], i + 1, files.length);
    }
    picker.value = '';
    setTimeout(() => { progSec.style.display = 'none'; }, 2000);
    loadFiles();
  }

  function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  function uploadFile(file, idx, total) {
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      const formData = new FormData();
      formData.append('file', file, file.name);

      pBar.style.width = '0%';
      let lastLoaded = 0;
      let lastTime = performance.now();

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          const percent = Math.round((event.loaded / event.total) * 100);
          pBar.style.width = percent + '%';

          const now = performance.now();
          const elapsed = (now - lastTime) / 1000;
          if (elapsed >= 0.3) {
            const speed = (event.loaded - lastLoaded) / elapsed;
            sText.innerText = `[${idx}/${total}] ${file.name} • ${percent}% (${formatBytes(speed)}/s)`;
            lastLoaded = event.loaded;
            lastTime = now;
          }
        }
      };

      xhr.onload = () => {
        sText.innerText = `✅ Transferred: ${file.name}`;
        resolve();
      };
      xhr.onerror = () => {
        sText.innerText = `❌ Error transferring: ${file.name}`;
        resolve();
      };

      xhr.open('POST', '/api/upload', true);
      xhr.send(formData);
    });
  }

  async function loadFiles() {
    try {
      const res = await fetch('/api/files');
      const files = await res.json();
      const list = document.getElementById('fileList');
      if (!files.length) {
        list.innerHTML = '<li style="color: var(--muted); font-size: 0.9rem;">No files uploaded yet</li>';
        return;
      }
      list.innerHTML = files.map(f => `
        <li>
          <div class="file-info">
            <span class="file-name" title="${f.name}">${f.name}</span>
            <span class="file-size">${formatBytes(f.size)}</span>
          </div>
          <a class="dl-btn" href="/api/download/${encodeURIComponent(f.name)}" download>Download</a>
        </li>
      `).join('');
    } catch (e) {
      console.error(e);
    }
  }

  loadFiles();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def get_index():
  return HTML_TEMPLATE


@app.get("/api/files")
async def list_files():
  items = []
  for f in BASE_DIR.iterdir():
    if f.is_file() and not f.name.startswith("."):
      items.append({
          "name": f.name,
          "size": f.stat().st_size,
          "mtime": f.stat().st_mtime,
      })
  # Sort newest files to top
  items.sort(key=lambda x: x["mtime"], reverse=True)
  return items


@app.post("/api/upload")
async def handle_upload(file: UploadFile = File(...)):
  safe_filename = Path(file.filename).name
  if not safe_filename or safe_filename.startswith("."):
    raise HTTPException(status_code=400, detail="Invalid file name")

  dest = BASE_DIR / safe_filename

  # High-throughput asynchronous chunk writing
  with open(dest, "wb") as buffer:
    while chunk := await file.read(CHUNK_SIZE):
      buffer.write(chunk)

  return {"status": "ok", "filename": safe_filename}


@app.get("/api/download/{filename}")
async def download_file(filename: str):
  safe_name = Path(urllib.parse.unquote(filename)).name
  path = (BASE_DIR / safe_name).resolve()

  # Ensure path stays strictly inside BASE_DIR
  if not str(path).startswith(str(BASE_DIR)) or not path.is_file():
    raise HTTPException(status_code=404, detail="File not found")

  media_type, _ = mimetypes.guess_type(str(path))
  if not media_type:
    media_type = "application/octet-stream"

  return FileResponse(
      path=path,
      filename=safe_name,
      media_type=media_type,
      headers={"Accept-Ranges": "bytes"},
  )


if __name__ == "__main__":
  ip = get_local_ip()
  url = f"http://{ip}:{PORT}"

  print("\n" + "=" * 54)
  print(f"  ⚡ SPEEDDROP LAN SERVER READY")
  print(f"  📱 iPhone Safari URL: {url}")
  print(f"  📁 Local Storage Directory: {BASE_DIR}")
  print("=" * 54 + "\n")

  qr = qrcode.QRCode(border=1)
  qr.add_data(url)
  qr.print_ascii(invert=True)

  uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")