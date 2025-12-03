from flask import Flask, Response, request
import subprocess
import json
import os
import logging
import time
import threading
from pathlib import Path
import random

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# -----------------------------
# CONFIG
# -----------------------------
COOKIE_FILE = "/mnt/data/cookies.txt"
TMP_DIR = Path("/tmp/ytmp3")
TMP_DIR.mkdir(exist_ok=True, parents=True)

REFRESH_INTERVAL = 600        # seconds between cache refresh
RECHECK_INTERVAL = 1200       # seconds before checking MP3 needs update
CLEANUP_INTERVAL = 1800       # seconds between cleanup runs
EXPIRE_AGE = 7200             # seconds to keep old MP3 files

CHANNELS = {
    "dhruv": "https://www.youtube.com/@dhruvrathee/videos"
}

VIDEO_CACHE = {name: {"url": None, "thumbnail": "", "last_checked": 0} for name in CHANNELS}
LAST_VIDEO_ID = {name: None for name in CHANNELS}

# -----------------------------
# Load cookies from file
# -----------------------------
if os.path.exists(COOKIE_FILE):
    logging.info(f"Using existing cookies file: {COOKIE_FILE}")
else:
    logging.warning(f"Cookies file {COOKIE_FILE} not found! Some videos may fail.")

# -----------------------------
# Cleanup old files
# -----------------------------
def cleanup_old_files():
    while True:
        now = time.time()
        for f in TMP_DIR.glob("*.mp3"):
            if now - f.stat().st_mtime > EXPIRE_AGE:
                try:
                    f.unlink()
                    logging.info(f"Deleted old file: {f}")
                except Exception as e:
                    logging.warning(f"Failed to delete {f}: {e}")
        time.sleep(CLEANUP_INTERVAL)

# -----------------------------
# Fetch latest video URL
# -----------------------------
def fetch_latest_video_url(name, channel_url):
    cmd = [
        "yt-dlp",
        "--dump-single-json",
        "--playlist-end", "1",
        "--no-warnings",
        "--compat-options", "no-youtube-unavailable-videos",
        channel_url
    ]
    if os.path.exists(COOKIE_FILE):
        cmd.insert(3, "--cookies")
        cmd.insert(4, COOKIE_FILE)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        video = data["entries"][0]
        video_id = video["id"]
        thumbnail = video.get("thumbnail", "")
        return f"https://www.youtube.com/watch?v={video_id}", thumbnail, video_id
    except Exception as e:
        logging.error(f"Failed to fetch latest video for {name}: {e}")
        return None, None, None

# -----------------------------
# Download & convert MP3
# -----------------------------
def download_and_convert(channel, video_url):
    final_path = TMP_DIR / f"{channel}.mp3"
    if final_path.exists():
        return final_path
    if not video_url:
        return None

    formats = ["91", "bestaudio"]
    for fmt in formats:
        try:
            cmd = [
                "yt-dlp",
                "-f", fmt,
                "--output", str(TMP_DIR / f"{channel}.%(ext)s"),
                "--extract-audio",
                "--audio-format", "mp3",
                "--postprocessor-args", "ffmpeg:-ar 22050 -ac 1 -b:a 40k",
                "--no-warnings",
                video_url
            ]
            if os.path.exists(COOKIE_FILE):
                cmd.insert(5, "--cookies")
                cmd.insert(6, COOKIE_FILE)

            subprocess.run(cmd, check=True)
            if final_path.exists():
                return final_path
        except Exception as e:
            logging.warning(f"Format {fmt} failed for {channel}: {e}")
    return None

# -----------------------------
# Stream generator
# -----------------------------
def generate_file(path, start=0, end=None, chunk_size=1024*1024):
    with open(path, "rb") as f:
        f.seek(start)
        remaining = (end - start + 1) if end else None
        while True:
            read_size = chunk_size if not remaining or remaining > chunk_size else remaining
            data = f.read(read_size)
            if not data:
                break
            yield data
            if remaining:
                remaining -= len(data)

# -----------------------------
# Background cache updater
# -----------------------------
def update_video_cache_loop():
    while True:
        for name, url in CHANNELS.items():
            video_url, thumbnail, video_id = fetch_latest_video_url(name, url)
            if video_url and video_id and LAST_VIDEO_ID[name] != video_id:
                LAST_VIDEO_ID[name] = video_id
                VIDEO_CACHE[name]["url"] = video_url
                VIDEO_CACHE[name]["thumbnail"] = thumbnail
                VIDEO_CACHE[name]["last_checked"] = time.time()
                download_and_convert(name, video_url)
            time.sleep(random.randint(3, 7))
        time.sleep(REFRESH_INTERVAL)

def auto_download_mp3s():
    while True:
        for name, data in VIDEO_CACHE.items():
            mp3_path = TMP_DIR / f"{name}.mp3"
            if data.get("url"):
                needs_update = (
                    not mp3_path.exists() or
                    time.time() - mp3_path.stat().st_mtime > RECHECK_INTERVAL
                )
                if needs_update:
                    download_and_convert(name, data["url"])
            time.sleep(random.randint(3, 8))
        time.sleep(RECHECK_INTERVAL)

# -----------------------------
# Flask Routes
# -----------------------------
@app.route("/<channel>.mp3")
def stream_mp3(channel):
    if channel not in CHANNELS:
        return "Channel not found", 404

    video_url = VIDEO_CACHE[channel].get("url")
    if not video_url:
        video_url, thumbnail, vid = fetch_latest_video_url(channel, CHANNELS[channel])
        if not video_url:
            return "Unable to fetch video", 500
        LAST_VIDEO_ID[channel] = vid
        VIDEO_CACHE[channel]["url"] = video_url
        VIDEO_CACHE[channel]["thumbnail"] = thumbnail
        VIDEO_CACHE[channel]["last_checked"] = time.time()

    mp3_path = download_and_convert(channel, video_url)
    if not mp3_path or not mp3_path.exists():
        return "Error preparing stream", 500

    file_size = os.path.getsize(mp3_path)
    headers = {'Content-Type': 'audio/mpeg', 'Accept-Ranges': 'bytes'}

    range_header = request.headers.get("Range")
    if range_header:
        start, end = range_header.strip().split("=")[1].split("-")
        start = int(start)
        end = int(end) if end else file_size - 1
        length = end - start + 1
        headers.update({
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(length)
        })
        return Response(generate_file(mp3_path, start, end), status=206, headers=headers)

    headers["Content-Length"] = str(file_size)
    return Response(generate_file(mp3_path), headers=headers)

@app.route("/")
def index():
    html = "<h3>Available Streams</h3><ul>"
    for channel in CHANNELS:
        mp3_path = TMP_DIR / f"{channel}.mp3"
        thumb = VIDEO_CACHE[channel].get("thumbnail") or "https://via.placeholder.com/120x80"
        status = "✔" if mp3_path.exists() else "⏳"
        html += f'''
        <li>
            <img src="{thumb}" height="80">
            <a href="/{channel}.mp3">{channel}</a> {status}
        </li>
        '''
    html += "</ul>"
    return html

# -----------------------------
# Start background threads
# -----------------------------
threading.Thread(target=update_video_cache_loop, daemon=True).start()
threading.Thread(target=cleanup_old_files, daemon=True).start()
threading.Thread(target=auto_download_mp3s, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
