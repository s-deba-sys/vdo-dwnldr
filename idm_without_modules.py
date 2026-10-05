import os
import re
import shutil
import threading
import queue
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
import yt_dlp


MEDIA_EXTS = (".m3u8", ".mpd", ".mp4", ".webm", ".m4v")
MEDIA_MIME = ("mpegurl", "dash+xml", "video/mp4", "video/webm")


def find_ffmpeg():
    """Find ffmpeg.exe using Windows PATH."""
    path = shutil.which("ffmpeg")
    if path:
        return path

    # Common WinGet location fallback.
    local = os.environ.get("LOCALAPPDATA", "")
    winget_root = os.path.join(
        local, "Microsoft", "WinGet", "Packages"
    )

    if os.path.isdir(winget_root):
        for root, dirs, files in os.walk(winget_root):
            if "ffmpeg.exe" in files:
                return os.path.join(root, "ffmpeg.exe")

    return None


FFMPEG_PATH = find_ffmpeg()


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
    """
    Create a useful grouping key.

    Query strings and changing HLS tokens are ignored where possible,
    preventing the same stream from appearing repeatedly.
    """
    p = urlparse(url)
    path = p.path

    if ".m3u8" in path.lower():
        path = path[:path.lower().find(".m3u8")] + ".m3u8"
    elif ".mpd" in path.lower():
        path = path[:path.lower().find(".mpd")] + ".mpd"

    # Some HLS servers expose the same stream through variants such
    # as /r6/... and /r6/s/.... Treat those as the same stream family.
    path_key = re.sub(r"/s/", "/", path, count=1)
    return p.netloc.lower() + path_key


def readable_source(url):
    p = urlparse(url)
    path = p.path

    # Keep the URL short and useful.
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

        self.download_dir = os.path.join(
            os.path.expanduser("~"), "Downloads"
        )

        self.found = []
        self.seen_keys = set()
        self.event_queue = queue.Queue()

        self.stop_requested = False
        self.browser_thread_obj = None

        self.build_ui()

        self.root.after(100, self.process_events)

        self.browser_thread_obj = threading.Thread(
            target=self.browser_thread,
            daemon=True
        )
        self.browser_thread_obj.start()

        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def build_ui(self):
        header = ttk.Frame(self.root, padding=12)
        header.pack(fill="x")

        ttk.Label(
            header,
            text="Media Downloader",
            font=("Segoe UI", 17, "bold")
        ).pack(side="left")

        self.status_var = tk.StringVar(
            value="Starting Chrome..."
        )

        ttk.Label(
            header,
            textvariable=self.status_var
        ).pack(side="right")

        # FFmpeg status
        ff = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        ff.pack(fill="x")

        if FFMPEG_PATH:
            ff_text = "FFmpeg: " + FFMPEG_PATH
        else:
            ff_text = "FFmpeg: NOT FOUND"

        self.ffmpeg_var = tk.StringVar(value=ff_text)

        ttk.Label(
            ff,
            textvariable=self.ffmpeg_var,
            foreground=("green" if FFMPEG_PATH else "red")
        ).pack(side="left")

        controls = ttk.Frame(
            self.root,
            padding=(12, 0, 12, 10)
        )
        controls.pack(fill="x")

        ttk.Button(
            controls,
            text="Choose Download Folder",
            command=self.choose_folder
        ).pack(side="left")

        self.folder_var = tk.StringVar(
            value=self.download_dir
        )

        ttk.Label(
            controls,
            textvariable=self.folder_var
        ).pack(side="left", padx=10)

        ttk.Button(
            controls,
            text="Refresh / Clear",
            command=self.clear_list
        ).pack(side="right")

        ttk.Button(
            controls,
            text="Download Selected",
            command=self.download_selected
        ).pack(side="right", padx=(0, 8))

        # Media table
        table_frame = ttk.Frame(self.root, padding=(12, 0, 12, 0))
        table_frame.pack(fill="both", expand=True)

        columns = (
            "number",
            "type",
            "source",
            "quality",
            "status"
        )

        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="extended"
        )

        settings = {
            "number": ("#", 55),
            "type": ("Type", 90),
            "source": ("Source", 540),
            "quality": ("Quality", 120),
            "status": ("Status", 180)
        }

        for col in columns:
            title, width = settings[col]
            self.tree.heading(col, text=title)
            self.tree.column(
                col,
                width=width,
                anchor="w"
            )

        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.tree.yview
        )

        self.tree.configure(
            yscrollcommand=scrollbar.set
        )

        self.tree.pack(
            side="left",
            fill="both",
            expand=True
        )

        scrollbar.pack(
            side="right",
            fill="y"
        )

        # Progress
        progress_frame = ttk.Frame(
            self.root,
            padding=12
        )
        progress_frame.pack(fill="x")

        self.progress = ttk.Progressbar(
            progress_frame,
            maximum=100,
            mode="determinate"
        )
        self.progress.pack(
            side="left",
            fill="x",
            expand=True
        )

        self.progress_var = tk.StringVar(
            value="Waiting..."
        )

        ttk.Label(
            progress_frame,
            textvariable=self.progress_var,
            width=32
        ).pack(side="right", padx=(10, 0))

        # Activity log
        log_frame = ttk.LabelFrame(
            self.root,
            text="Activity",
            padding=5
        )
        log_frame.pack(
            fill="x",
            padx=12,
            pady=(0, 12)
        )

        self.log = tk.Text(
            log_frame,
            height=7,
            wrap="word",
            state="disabled"
        )

        self.log.pack(
            fill="both",
            expand=True
        )

    def log_message(self, text):
        self.event_queue.put(("log", text))

    def choose_folder(self):
        folder = filedialog.askdirectory(
            initialdir=self.download_dir
        )

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

        item["kind"] = media_kind(
            item["url"],
            item.get("ctype", "")
        )

        item["quality"] = "Auto"

        self.found.append(item)

        self.event_queue.put(
            ("media", len(self.found), item)
        )

    def browser_thread(self):
        try:
            with sync_playwright() as p:

                browser = p.chromium.launch(
                    channel="chrome",
                    headless=False
                )

                context = browser.new_context(
                    no_viewport=True
                )

                def on_response(resp):
                    try:
                        url = resp.url
                        ctype = resp.headers.get(
                            "content-type",
                            ""
                        )

                        kind = media_kind(url, ctype)

                        if not kind:
                            return

                        req = resp.request

                        item = {
                            "url": url,
                            "ua": req.headers.get(
                                "user-agent",
                                ""
                            ),
                            "referer": req.headers.get(
                                "referer",
                                resp.frame.url
                                if resp.frame else ""
                            ),
                            "cookies": context.cookies(url),
                            "ctype": ctype,
                        }

                        self.add_media(item)

                    except Exception:
                        pass

                context.on(
                    "response",
                    on_response
                )

                page = context.new_page()

                self.event_queue.put(
                    (
                        "status",
                        "Chrome ready — browse to a video and press PLAY."
                    )
                )

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
            self.event_queue.put(
                (
                    "status",
                    "Browser error: " + str(e)
                )
            )

    def get_selected_items(self):
        selected = self.tree.selection()
        result = []

        for iid in selected:
            values = self.tree.item(
                iid,
                "values"
            )

            try:
                index = int(values[0]) - 1
                if 0 <= index < len(self.found):
                    result.append(
                        (self.found[index], iid)
                    )
            except Exception:
                pass

        return result

    def download_selected(self):
        if not FFMPEG_PATH:
            messagebox.showerror(
                "FFmpeg not found",
                "FFmpeg could not be found.\n\n"
                "Install FFmpeg and restart this program."
            )
            return

        selected = self.get_selected_items()

        if not selected:
            messagebox.showinfo(
                "Select media",
                "Select one or more rows first."
            )
            return

        for item, iid in selected:
            self.tree.set(
                iid,
                "status",
                "Starting..."
            )

            thread = threading.Thread(
                target=self.download,
                args=(item, iid),
                daemon=True
            )

            thread.start()

    def download(self, item, iid):
        cookie_header = "; ".join(
            f'{c["name"]}={c["value"]}'
            for c in item["cookies"]
        )

        stamp = time.strftime(
            "%Y%m%d_%H%M%S"
        )

        opts = {
            # IMPORTANT: do not use %(id)s here.
            # HLS URLs can contain extremely long tokens and Windows
            # will reject the resulting filename/path.
            "outtmpl": os.path.join(
                self.download_dir,
                f"video_{stamp}.%(ext)s"
            ),

            "windowsfilenames": True,
            "trim_file_name": 80,

            "http_headers": {
                "User-Agent": item["ua"],
                "Referer": item["referer"],
                "Cookie": cookie_header,
            },

            # Explicitly tell yt-dlp where FFmpeg is.
            "ffmpeg_location": FFMPEG_PATH,

            # FFmpeg handles HLS much better than the
            # native downloader for this use case.
            "downloader": "ffmpeg",

            "concurrent_fragment_downloads": 4,

            "quiet": True,
            "no_warnings": True,
            "noprogress": True,

            "progress_hooks": [
                lambda d: self.progress_hook(
                    d, iid
                )
            ],
        }

        try:
            self.log_message(
                "Starting: " +
                readable_source(item["url"])
            )

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download(
                    [item["url"]]
                )

            self.event_queue.put(
                (
                    "done",
                    iid,
                    "Completed"
                )
            )

        except Exception as e:
            error = str(e)

            self.event_queue.put(
                (
                    "done",
                    iid,
                    "Error"
                )
            )

            self.log_message(
                "Download error: " +
                error[:500]
            )

    def progress_hook(self, d, iid):
        status = d.get("status")

        if status == "downloading":

            percent = d.get(
                "_percent_str",
                "0%"
            )

            speed = d.get(
                "_speed_str",
                ""
            )

            eta = d.get(
                "_eta_str",
                ""
            )

            self.event_queue.put(
                (
                    "progress",
                    iid,
                    percent,
                    speed,
                    eta
                )
            )

        elif status == "finished":

            self.event_queue.put(
                (
                    "progress",
                    iid,
                    "100%",
                    "",
                    "processing..."
                )
            )

    def process_events(self):
        try:
            while True:

                event = (
                    self.event_queue
                    .get_nowait()
                )

                kind = event[0]

                if kind == "log":

                    self.log.configure(
                        state="normal"
                    )

                    self.log.insert(
                        "end",
                        event[1] + "\n"
                    )

                    self.log.see("end")

                    self.log.configure(
                        state="disabled"
                    )

                elif kind == "status":

                    self.status_var.set(
                        event[1]
                    )

                    self.log_message(
                        event[1]
                    )

                elif kind == "media":

                    _, number, item = event

                    iid = self.tree.insert(
                        "",
                        "end",
                        values=(
                            number,
                            item["kind"],
                            readable_source(
                                item["url"]
                            ),
                            "Auto",
                            "Ready"
                        )
                    )

                    item["tree_id"] = iid

                    self.log_message(
                        f"Detected {item['kind']}: "
                        f"{readable_source(item['url'])}"
                    )

                elif kind == "progress":

                    _, iid, percent, speed, eta = event

                    try:
                        numeric = float(
                            re.sub(
                                r"[^0-9.]",
                                "",
                                percent
                            )
                        )
                    except Exception:
                        numeric = 0

                    self.progress["value"] = numeric

                    self.progress_var.set(
                        f"{percent}   "
                        f"{speed}   "
                        f"ETA {eta}"
                    )

                    self.tree.set(
                        iid,
                        "status",
                        f"Downloading {percent}"
                    )

                elif kind == "done":

                    _, iid, status = event

                    self.tree.set(
                        iid,
                        "status",
                        status
                    )

                    if status == "Completed":
                        self.progress["value"] = 100
                        self.progress_var.set(
                            "Completed"
                        )

        except queue.Empty:
            pass

        if not self.stop_requested:
            self.root.after(
                100,
                self.process_events
            )

    def close(self):
        self.stop_requested = True

        try:
            self.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    root = tk.Tk()

    try:
        root.tk.call(
            "tk",
            "scaling",
            1.15
        )
    except Exception:
        pass

    app = MediaDownloaderApp(root)
    root.mainloop()
