import os
import sys
import shutil
import subprocess
import urllib.request
import zipfile
import threading
import queue
import time
import re
from urllib.parse import urlparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# ==========================================
# PATHS & CONFIGURATION
# ==========================================
APP_DATA_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "MediaDownloaderData"
)
BIN_DIR = os.path.join(APP_DATA_DIR, "bin")
FFMPEG_PATH = os.path.join(BIN_DIR, "ffmpeg.exe")
BROWSERS_DIR = os.path.join(APP_DATA_DIR, "browsers")

os.environ["PLAYWRIGHT_BROWSERS_PATH"] = BROWSERS_DIR

MEDIA_EXTS = (".m3u8", ".mpd", ".mp4", ".webm", ".m4v")
MEDIA_MIME = ("mpegurl", "dash+xml", "video/mp4", "video/webm")


# ==========================================
# BOOTSTRAP / AUTO-SETUP SPLASH SCREEN
# ==========================================
class BootstrapSplash:
    def __init__(self, root, on_complete):
        self.root = root
        self.on_complete = on_complete
        self.root.title("Media Downloader - Initial Setup")
        self.root.geometry("480x180")
        self.root.resizable(False, False)

        os.makedirs(BIN_DIR, exist_ok=True)
        os.makedirs(BROWSERS_DIR, exist_ok=True)

        self.label = ttk.Label(
            root,
            text="Setting up required components (one-time setup)...",
            font=("Segoe UI", 10, "bold")
        )
        self.label.pack(pady=(20, 8), padx=20, anchor="w")

        self.progress = ttk.Progressbar(root, length=440, mode="determinate")
        self.progress.pack(pady=5, padx=20)

        self.status = ttk.Label(
            root,
            text="Checking libraries...",
            font=("Segoe UI", 9),
            foreground="#555"
        )
        self.status.pack(pady=(5, 10), padx=20, anchor="w")

        threading.Thread(target=self.run_bootstrap, daemon=True).start()

    def update(self, text, percent=None):
        def _apply():
            self.status.config(text=text)
            if percent is not None:
                self.progress["value"] = percent
        self.root.after(0, _apply)

    def run_bootstrap(self):
        try:
            # 1. Install missing python packages
            self.update("Checking Python packages (yt-dlp, playwright)...", 10)
            required = ["yt-dlp", "playwright"]
            missing = []
            for pkg in required:
                try:
                    __import__(pkg.replace("-", "_"))
                except ImportError:
                    missing.append(pkg)

            if missing:
                self.update(f"Installing missing packages: {', '.join(missing)}...", 20)
                cmd = [sys.executable, "-m", "pip", "install", *missing]
                creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags)
                if res.returncode != 0:
                    raise RuntimeError(f"Pip install failed:\n{res.stderr.decode('utf-8', errors='ignore')}")

            # 2. Check and download FFmpeg
            needs_ffmpeg = not (os.path.isfile(FFMPEG_PATH) or shutil.which("ffmpeg"))
            if needs_ffmpeg:
                self.download_ffmpeg()

            # 3. Check and install Chromium
            self.install_chromium()

            self.update("All components ready! Starting...", 100)
            self.root.after(600, self.finish)

        except Exception as e:
            err_msg = str(e)
            self.root.after(0, lambda: messagebox.showerror("Setup Error", f"Failed to initialize:\n{err_msg}"))
            self.root.after(0, self.root.destroy)

    def download_ffmpeg(self):
        self.update("Downloading FFmpeg converter (~35MB)...", 35)
        # Using Gyan.dev essentials build
        url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        zip_path = os.path.join(BIN_DIR, "ffmpeg.zip")

        def reporthook(block_num, block_size, total_size):
            if total_size > 0:
                downloaded = block_num * block_size
                pct = min(35 + int((downloaded / total_size) * 35), 70)
                self.update(
                    f"Downloading FFmpeg: {downloaded // (1024 * 1024)} MB / {total_size // (1024 * 1024)} MB",
                    pct
                )

        urllib.request.urlretrieve(url, zip_path, reporthook=reporthook)

        self.update("Extracting FFmpeg...", 72)
        with zipfile.ZipFile(zip_path, "r") as zf:
            for item in zf.namelist():
                if item.endswith("bin/ffmpeg.exe"):
                    with zf.open(item) as src, open(FFMPEG_PATH, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    break

        if os.path.exists(zip_path):
            os.remove(zip_path)

    def install_chromium(self):
        self.update("Checking browser engine...", 80)
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags)
        if res.returncode != 0:
            raise RuntimeError(f"Browser installation failed:\n{res.stderr.decode('utf-8', errors='ignore')}")

    def finish(self):
        self.root.destroy()
        self.on_complete()


# ==========================================
# MAIN APPLICATION LOGIC
# ==========================================
def resolve_ffmpeg():
    if os.path.isfile(FFMPEG_PATH):
        return FFMPEG_PATH
    return shutil.which("ffmpeg")


def media_kind(url, ctype=""):
    path = urlparse(url).path.lower()
    ctype = (ctype or "").lower()

    if ".m3u8" in path or "mpegurl" in ctype:
        return "HLS"
    if ".mpd" in path or "dash+xml" in ctype:
        return "DASH"
    if path.endswith((".mp4", ".webm", ".m4v")):
        return "MP4"
    return None


def stream_key(url):
    p = urlparse(url)
    path = p.path

    if ".m3u8" in path.lower():
        path = path[:path.lower().find(".m3u8")] + ".m3u8"
    elif ".mpd" in path.lower():
        path = path[:path.lower().find(".mpd")] + ".mpd"

    path_key = re.sub(r"/s/", "/", path, count=1)
    return p.netloc.lower() + path_key


def readable_source(url):
    p = urlparse(url)
    path = p.path

    if ".m3u8" in path.lower():
        name = path.split("/")[-1]
        return f"{p.netloc} / HLS / {name[:45]}"
    if ".mpd" in path.lower():
        name = path.split("/")[-1]
        return f"{p.netloc} / DASH / {name[:45]}"

    name = os.path.basename(path)
    return f"{p.netloc} / {name[:60]}"


class MediaDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Media Downloader")
        self.root.geometry("1100x700")
        self.root.minsize(900, 550)

        self.ffmpeg_exec = resolve_ffmpeg()
        self.download_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        self.found = []
        self.seen_keys = set()
        self.event_queue = queue.Queue()

        self.stop_requested = False
        self.browser_thread_obj = None

        self.build_ui()
        self.root.after(100, self.process_events)

        self.browser_thread_obj = threading.Thread(target=self.browser_thread, daemon=True)
        self.browser_thread_obj.start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def build_ui(self):
        header = ttk.Frame(self.root, padding=12)
        header.pack(fill="x")

        ttk.Label(header, text="Media Downloader", font=("Segoe UI", 17, "bold")).pack(side="left")
        self.status_var = tk.StringVar(value="Starting browser...")
        ttk.Label(header, textvariable=self.status_var).pack(side="right")

        # FFmpeg status
        ff = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        ff.pack(fill="x")
        ff_text = f"FFmpeg: {self.ffmpeg_exec}" if self.ffmpeg_exec else "FFmpeg: NOT FOUND"
        self.ffmpeg_var = tk.StringVar(value=ff_text)
        ttk.Label(
            ff,
            textvariable=self.ffmpeg_var,
            foreground=("green" if self.ffmpeg_exec else "red")
        ).pack(side="left")

        # Controls
        controls = ttk.Frame(self.root, padding=(12, 0, 12, 10))
        controls.pack(fill="x")

        ttk.Button(controls, text="Choose Download Folder", command=self.choose_folder).pack(side="left")
        self.folder_var = tk.StringVar(value=self.download_dir)
        ttk.Label(controls, textvariable=self.folder_var).pack(side="left", padx=10)

        ttk.Button(controls, text="Refresh / Clear", command=self.clear_list).pack(side="right")
        ttk.Button(controls, text="Download Selected", command=self.download_selected).pack(side="right", padx=(0, 8))

        # Media Table
        table_frame = ttk.Frame(self.root, padding=(12, 0, 12, 0))
        table_frame.pack(fill="both", expand=True)

        columns = ("number", "type", "source", "quality", "status")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")

        settings = {
            "number": ("#", 55),
            "type": ("Type", 90),
            "source": ("Source", 540),
            "quality": ("Quality", 120),
            "status": ("Status", 180),
        }

        for col in columns:
            title, width = settings[col]
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="w")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Progress
        progress_frame = ttk.Frame(self.root, padding=12)
        progress_frame.pack(fill="x")

        self.progress = ttk.Progressbar(progress_frame, maximum=100, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)

        self.progress_var = tk.StringVar(value="Waiting...")
        ttk.Label(progress_frame, textvariable=self.progress_var, width=32).pack(side="right", padx=(10, 0))

        # Activity Log
        log_frame = ttk.LabelFrame(self.root, text="Activity", padding=5)
        log_frame.pack(fill="x", padx=12, pady=(0, 12))

        self.log = tk.Text(log_frame, height=7, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)

    def log_message(self, text):
        self.event_queue.put(("log", text))

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.download_dir)
        if folder:
            self.download_dir = folder
            self.folder_var.set(folder)

    def clear_list(self):
        self.found.clear()
        self.seen_keys.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.progress["value"] = 0
        self.progress_var.set("Waiting...")
        self.log_message("Media list cleared.")

    def add_media(self, item):
        key = stream_key(item["url"])
        if key in self.seen_keys:
            return

        self.seen_keys.add(key)
        item["kind"] = media_kind(item["url"], item.get("ctype", ""))
        item["quality"] = "Auto"
        self.found.append(item)
        self.event_queue.put(("media", len(self.found), item))

    def browser_thread(self):
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(channel="chrome", headless=False)
                except Exception:
                    browser = p.chromium.launch(headless=False)

                context = browser.new_context(no_viewport=True)

                def on_response(resp):
                    try:
                        url = resp.url
                        ctype = resp.headers.get("content-type", "")
                        kind = media_kind(url, ctype)
                        if not kind:
                            return

                        req = resp.request
                        item = {
                            "url": url,
                            "ua": req.headers.get("user-agent", ""),
                            "referer": req.headers.get("referer", resp.frame.url if resp.frame else ""),
                            "cookies": context.cookies(url),
                            "ctype": ctype,
                        }
                        self.add_media(item)
                    except Exception:
                        pass

                context.on("response", on_response)
                page = context.new_page()

                self.event_queue.put(("status", "Browser ready — browse to a video and press PLAY."))

                while not self.stop_requested:
                    try:
                        page.wait_for_timeout(250)
                    except Exception:
                        break

                try:
                    browser.close()
                except Exception:
                    pass

        except Exception as e:
            self.event_queue.put(("status", f"Browser error: {e}"))

    def get_selected_items(self):
        selected = self.tree.selection()
        result = []
        for iid in selected:
            values = self.tree.item(iid, "values")
            try:
                index = int(values[0]) - 1
                if 0 <= index < len(self.found):
                    result.append((self.found[index], iid))
            except Exception:
                pass
        return result

    def download_selected(self):
        if not self.ffmpeg_exec:
            messagebox.showerror(
                "FFmpeg not found",
                "FFmpeg could not be found.\n\nRestart the script to allow automatic download."
            )
            return

        selected = self.get_selected_items()
        if not selected:
            messagebox.showinfo("Select media", "Select one or more rows first.")
            return

        for item, iid in selected:
            self.tree.set(iid, "status", "Starting...")
            thread = threading.Thread(target=self.download, args=(item, iid), daemon=True)
            thread.start()

    def download(self, item, iid):
        import yt_dlp

        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in item["cookies"])
        stamp = time.strftime("%Y%m%d_%H%M%S")

        opts = {
            "outtmpl": os.path.join(self.download_dir, f"video_{stamp}.%(ext)s"),
            "windowsfilenames": True,
            "trim_file_name": 80,
            "http_headers": {
                "User-Agent": item["ua"],
                "Referer": item["referer"],
                "Cookie": cookie_header,
            },
            "ffmpeg_location": self.ffmpeg_exec,
            "downloader": "ffmpeg",
            "concurrent_fragment_downloads": 4,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [lambda d: self.progress_hook(d, iid)],
        }

        try:
            self.log_message(f"Starting: {readable_source(item['url'])}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([item["url"]])
            self.event_queue.put(("done", iid, "Completed"))
        except Exception as e:
            self.event_queue.put(("done", iid, "Error"))
            self.log_message(f"Download error: {str(e)[:500]}")

    def progress_hook(self, d, iid):
        status = d.get("status")
        if status == "downloading":
            percent = d.get("_percent_str", "0%")
            speed = d.get("_speed_str", "")
            eta = d.get("_eta_str", "")
            self.event_queue.put(("progress", iid, percent, speed, eta))
        elif status == "finished":
            self.event_queue.put(("progress", iid, "100%", "", "processing..."))

    def process_events(self):
        try:
            while True:
                event = self.event_queue.get_nowait()
                kind = event[0]

                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", event[1] + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")

                elif kind == "status":
                    self.status_var.set(event[1])
                    self.log_message(event[1])

                elif kind == "media":
                    _, number, item = event
                    iid = self.tree.insert(
                        "",
                        "end",
                        values=(number, item["kind"], readable_source(item["url"]), "Auto", "Ready")
                    )
                    item["tree_id"] = iid
                    self.log_message(f"Detected {item['kind']}: {readable_source(item['url'])}")

                elif kind == "progress":
                    _, iid, percent, speed, eta = event
                    try:
                        numeric = float(re.sub(r"[^0-9.]", "", percent))
                    except Exception:
                        numeric = 0

                    self.progress["value"] = numeric
                    self.progress_var.set(f"{percent}   {speed}   ETA {eta}")
                    self.tree.set(iid, "status", f"Downloading {percent}")

                elif kind == "done":
                    _, iid, status = event
                    self.tree.set(iid, "status", status)
                    if status == "Completed":
                        self.progress["value"] = 100
                        self.progress_var.set("Completed")

        except queue.Empty:
            pass

        if not self.stop_requested:
            self.root.after(100, self.process_events)

    def close(self):
        self.stop_requested = True
        try:
            self.root.destroy()
        except Exception:
            pass


# ==========================================
# ENTRY POINT
# ==========================================
def launch_main_gui():
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.15)
    except Exception:
        pass
    MediaDownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    # Check if packages & binaries already exist
    packages_ready = True
    for p in ["yt_dlp", "playwright"]:
        try:
            __import__(p)
        except ImportError:
            packages_ready = False
            break

    binaries_ready = (
        (os.path.isfile(FFMPEG_PATH) or shutil.which("ffmpeg")) and
        os.path.isdir(BROWSERS_DIR) and
        bool(os.listdir(BROWSERS_DIR))
    )

    if not (packages_ready and binaries_ready):
        splash_root = tk.Tk()
        BootstrapSplash(splash_root, on_complete=launch_main_gui)
        splash_root.mainloop()
    else:
        launch_main_gui()
