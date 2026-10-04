import io
import os
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlparse
from PIL import Image
import streamlit as st
import yt_dlp
from playwright.sync_api import sync_playwright

# -----------------------------------------------------------------------------
# 1. CLOUD BOOTSTRAP: Ensure Playwright Chromium binary is installed
# -----------------------------------------------------------------------------
@st.cache_resource
def install_playwright_browsers():
    """Installs Chromium binaries inside Streamlit container once on boot."""
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception as e:
        st.error(f"Failed to bootstrap Playwright: {e}")

install_playwright_browsers()

# -----------------------------------------------------------------------------
# 2. STREAM DETECTION & CLASSIFICATION
# -----------------------------------------------------------------------------
def get_media_kind(url: str, ctype: str = "") -> str:
    path = urlparse(url).path.lower()
    ctype = (ctype or "").lower()
    if ".m3u8" in path or "mpegurl" in ctype:
        return "HLS (.m3u8)"
    if ".mpd" in path or "dash+xml" in ctype:
        return "DASH (.mpd)"
    if path.endswith((".mp4", ".webm", ".m4v")) or any(m in ctype for m in ("video/mp4", "video/webm")):
        return "Direct MP4/WebM"
    return None

# -----------------------------------------------------------------------------
# 3. BROWSER SESSION MANAGER
# -----------------------------------------------------------------------------
VIEWPORT_W = 1280
VIEWPORT_H = 720

def init_browser():
    """Starts or resumes a persistent browser session in Streamlit session_state."""
    if "pw_instance" not in st.session_state:
        st.session_state.pw_instance = sync_playwright().start()
        st.session_state.browser = st.session_state.pw_instance.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required"
            ]
        )
        st.session_state.context = st.session_state.browser.new_context(
            viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        st.session_state.page = st.session_state.context.new_page()

        # Sniff network requests on background responses
        def on_response(resp):
            try:
                url = resp.url
                ctype = resp.headers.get("content-type", "")
                kind = get_media_kind(url, ctype)
                if kind:
                    existing_urls = [item["url"] for item in st.session_state.captured_streams]
                    if url not in existing_urls:
                        req = resp.request
                        st.session_state.captured_streams.append({
                            "url": url,
                            "kind": kind,
                            "ua": req.headers.get("user-agent", ""),
                            "referer": req.headers.get("referer", resp.frame.url if resp.frame else ""),
                            "cookies": st.session_state.context.cookies(url),
                        })
            except Exception:
                pass

        st.session_state.page.on("response", on_response)

if "captured_streams" not in st.session_state:
    st.session_state.captured_streams = []

init_browser()

# -----------------------------------------------------------------------------
# 4. DOWNLOAD ENGINE WITH RESOLUTION SELECTION
# -----------------------------------------------------------------------------
def download_stream(item: dict, quality_preset: str, progress_bar, status_text):
    temp_dir = tempfile.mkdtemp()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_template = os.path.join(temp_dir, f"video_{stamp}.%(ext)s")

    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in item.get("cookies", []))

    # Format resolution string for yt-dlp
    if quality_preset == "Highest (1080p/4K)":
        format_selector = "bestvideo+bestaudio/best"
    elif quality_preset == "720p":
        format_selector = "bestvideo[height<=720]+bestaudio/best[height<=720]"
    elif quality_preset == "480p":
        format_selector = "bestvideo[height<=480]+bestaudio/best[height<=480]"
    else:
        format_selector = "best"

    def progress_hook(d):
        status = d.get("status")
        if status == "downloading":
            raw_percent = d.get("_percent_str", "0%").strip()
            clean_percent = "".join(ch for ch in raw_percent if ch.isdigit() or ch == ".")
            try:
                val = min(100.0, max(0.0, float(clean_percent)))
                progress_bar.progress(int(val))
            except ValueError:
                pass
            speed = d.get("_speed_str", "")
            eta = d.get("_eta_str", "")
            status_text.caption(f"⚡ Downloading: {raw_percent} | Speed: {speed} | ETA: {eta}")
        elif status == "finished":
            progress_bar.progress(100)
            status_text.caption("⚙️ Merging video & audio tracks via FFmpeg...")

    ydl_opts = {
        "outtmpl": out_template,
        "format": format_selector,
        "http_headers": {
            "User-Agent": item.get("ua", ""),
            "Referer": item.get("referer", ""),
            "Cookie": cookie_header,
        },
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([item["url"]])

        downloaded_files = [
            os.path.join(temp_dir, f) for f in os.listdir(temp_dir)
            if not f.endswith(".part") and not f.endswith(".ytdl")
        ]
        if downloaded_files:
            return downloaded_files[0]
    except Exception as e:
        status_text.error(f"Download failed: {e}")
    return None

# -----------------------------------------------------------------------------
# 5. STREAMLIT UI: REMOTE CONTROL & MOBILE DOWNLOADS
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Remote Media Sniffer", page_icon="🎬", layout="centered")

st.markdown("""
<style>
    .block-container { padding-top: 1.2rem; padding-bottom: 3.5rem; max-width: 720px; }
    .stButton>button { width: 100%; border-radius: 8px; font-weight: 600; }
    .control-box { background: #1e293b; padding: 12px; border-radius: 10px; margin-bottom: 12px; }
</style>
""", unsafe_allow_html=True)

st.title("🎬 Remote Web Navigator & Sniffer")
st.caption("Visually interact with web video players to trigger 1080p quality, capture streams, and download on mobile.")

# URL Navigation Bar
nav_col1, nav_col2 = st.columns([3, 1])
with nav_col1:
    target_url = st.text_input("Target Web URL:", placeholder="https://example.com/video")
with nav_col2:
    st.write(" ")
    if st.button("🌐 Navigate", use_container_width=True):
        if target_url:
            with st.spinner("Loading webpage..."):
                try:
                    st.session_state.page.goto(target_url, wait_until="domcontentloaded", timeout=40000)
                    time.sleep(2)
                except Exception as e:
                    st.error(f"Navigation error: {e}")

# Remote Viewport Screen
page = st.session_state.page
try:
    screenshot_bytes = page.screenshot()
    img = Image.open(io.BytesIO(screenshot_bytes))
    st.image(img, caption="Live Browser Viewport (1280 × 720)", use_container_width=True)
except Exception:
    st.info("No active page loaded yet. Enter a URL above and click Navigate.")

# --- REMOTE CONTROL PANEL ---
st.markdown("### 🖱️ Touch & Player Controls")

# Row 1: Quick Actions
act1, act2, act3 = st.columns(3)
with act1:
    if st.button("▶️ Force Play All"):
        page.evaluate("""() => {
            document.querySelectorAll('video').forEach(v => { v.muted = false; v.play().catch(() => {}); });
        }""")
        time.sleep(1)
        st.rerun()
with act2:
    if st.button("⏸️ Pause All"):
        page.evaluate("() => { document.querySelectorAll('video').forEach(v => v.pause()); }")
        st.rerun()
with act3:
    if st.button("📜 Scroll Down"):
        page.evaluate("() => window.scrollBy(0, 500)")
        time.sleep(1)
        st.rerun()

# Row 2: Precision Click Coordinates
with st.expander("🎯 Precision Coordinate Tap", expanded=True):
    st.caption("Inspect the image above to target the gear icon, play button, or resolution dropdown.")
    coord_col1, coord_col2, coord_col3 = st.columns([1, 1, 1])
    with coord_col1:
        click_x = st.number_input("X Coordinate", min_value=0, max_value=VIEWPORT_W, value=640, step=10)
    with coord_col2:
        click_y = st.number_input("Y Coordinate", min_value=0, max_value=VIEWPORT_H, value=360, step=10)
    with coord_col3:
        st.write(" ")
        if st.button("👆 Tap Coordinates", use_container_width=True):
            page.mouse.click(click_x, click_y)
            time.sleep(1.5)  # Allow time for quality menu / popup to open
            st.rerun()

# Row 3: Preset Hotspots for Common Web Players
preset_col1, preset_col2 = st.columns(2)
with preset_col1:
    if st.button("⚙️ Bottom-Right (Player Bar)"):
        # Most player settings/gear icons sit around X: 1210-1250, Y: 680-700
        page.mouse.click(1220, 685)
        time.sleep(1.2)
        st.rerun()
with preset_col2:
    if st.button("🔄 Refresh View"):
        st.rerun()

st.markdown("---")

# -----------------------------------------------------------------------------
# 6. DETECTED STREAM QUEUE & MOBILE DOWNLOAD
# -----------------------------------------------------------------------------
streams = st.session_state.captured_streams
st.markdown(f"### 📡 Captured Media Streams ({len(streams)})")

if not streams:
    st.info("No streams captured yet. Tap **Force Play All** or click the player to trigger stream initialization.")
else:
    if st.button("🗑️ Clear Stream History"):
        st.session_state.captured_streams = []
        st.rerun()

    for idx, item in enumerate(streams):
        domain = urlparse(item["url"]).netloc
        with st.expander(f"#{idx + 1} | {item['kind']} ({domain})", expanded=True):
            st.code(item["url"][:110] + ("..." if len(item["url"]) > 110 else ""), language="text")

            q_col, dl_col = st.columns([1, 1])
            with q_col:
                quality_choice = st.selectbox(
                    "Target Resolution:",
                    ["Highest (1080p/4K)", "720p", "480p", "Auto/Best"],
                    key=f"qual_{idx}"
                )

            with dl_col:
                st.write(" ")
                fetch_clicked = st.button(f"⚡ Assemble Video #{idx + 1}", key=f"fetch_{idx}")

            pbar = st.empty()
            status_box = st.empty()

            if fetch_clicked:
                bar = pbar.progress(0)
                file_path = download_stream(item, quality_choice, bar, status_box)

                if file_path and os.path.exists(file_path):
                    status_box.success("File ready for mobile storage!")
                    filename = os.path.basename(file_path)

                    with open(file_path, "rb") as f:
                        file_data = f.read()

                    st.download_button(
                        label=f"💾 Save {filename} to iPhone / Android",
                        data=file_data,
                        file_name=filename,
                        mime="video/mp4",
                        key=f"dl_btn_{idx}",
                        use_container_width=True
                    )

                    try:
                        shutil.rmtree(os.path.dirname(file_path), ignore_errors=True)
                    except Exception:
                        pass
