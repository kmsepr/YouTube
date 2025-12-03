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

# INTERVALS
REFRESH_INTERVAL = 600
RECHECK_INTERVAL = 1200
CLEANUP_INTERVAL = 1800
EXPIRE_AGE = 7200

CHANNELS = {
    "dhruv": "https://www.youtube.com/@dhruvrathee/videos"
}

VIDEO_CACHE = {
    name: {"url": None, "thumbnail": "", "last_checked": 0}
    for name in CHANNELS
}

LAST_VIDEO_ID = {name: None for name in CHANNELS}

TMP_DIR = Path("/tmp/ytmp3")
TMP_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------
def cleanup_old_files():
    while True:
        now = time.time()
        for f in TMP_DIR.glob("*.mp3"):
            if now - f.stat().st_mtime > EXPIRE_AGE:
                try:
                    f.unlink()
                except:
                    pass
        time.sleep(CLEANUP_INTERVAL)


# ---------------------------------------------------------------------
def fetch_latest_video_url(name, channel_url):
    try:
        result = subprocess.run([
            "yt-dlp",
            "--dump-single-json",
            "--playlist-end", "1",
            "--no-warnings",
            "--cookies", "/mnt/data/cookies.txt",
            "--compat-options", "no-youtube-unavailable-videos",
            channel_url
        ], capture_output=True, text=True, check=True)

        data = json.loads(result.stdout)
        video = data["entries"][0]
        video_id = video["id"]
        thumbnail = video.get("thumbnail", "")

        return f"https://www.youtube.com/watch?v={video_id}", thumbnail, video_id

    except Exception as e:
        logging.error(f"Fetch failed: {e}")
        return None, None, None


# ---------------------------------------------------------------------
def download_and_convert(channel, video_url):
    final_path = TMP_DIR / f"{channel}.mp3"

    if final_path.exists():
        return final_path

    if not video_url:
        return None

    try:
        subprocess.run([
            "yt-dlp",
            "-f", "91",  # FORCE FORMAT 91
            "--output", str(TMP_DIR / f"{channel}.%(ext)s"),
            "--extract-audio",
            "--audio-format", "mp3",
            "--postprocessor-args", "ffmpeg:-ar 22050 -ac 1 -b:a 40k",
            "--cookies", "/mnt/data/cookies.txt",
            "--no-warnings",
            video_url
        ], check=True)

        return final_path if final_path.exists() else None

    except Exception as e:
        logging.error(f"Download failed for {channel}: {e}")
        return None


# ---------------------------------------------------------------------
def update_video_cache_loop():
    while True:
        for name, url in CHANNELS.items():
            video_url, thumbnail, video_id = fetch_latest_video_url(name, url)

            if video_url and video_id:
                if LAST_VIDEO_ID[name] != video_id:
                    LAST_VIDEO_ID[name] = video_id
                    VIDEO_CACHE[name]["url"] = video_url
                    VIDEO_CACHE[name]["thumbnail"] = thumbnail
                    VIDEO_CACHE[name]["last_checked"] = time.time()
                    download_and_convert(name, video_url)

            time.sleep(random.randint(3, 7))

        time.sleep(REFRESH_INTERVAL)


# ---------------------------------------------------------------------
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


# ---------------------------------------------------------------------
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
        byte1, byte2 = range_header.strip().split("=")[1].split("-")
        byte1 = int(byte1)
        byte2 = int(byte2) if byte2 else file_size - 1
        length = byte2 - byte1 + 1

        with open(mp3_path, "rb") as f:
            f.seek(byte1)
            data = f.read(length)

        headers.update({
            "Content-Range": f"bytes {byte1}-{byte2}/{file_size}",
            "Content-Length": str(length)
        })
        return Response(data, 206, headers)

    with open(mp3_path, "rb") as f:
        data = f.read()

    headers["Content-Length"] = str(file_size)
    return Response(data, headers=headers)


# ---------------------------------------------------------------------
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


# Background threads
threading.Thread(target=update_video_cache_loop, daemon=True).start()
threading.Thread(target=cleanup_old_files, daemon=True).start()
threading.Thread(target=auto_download_mp3s, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
