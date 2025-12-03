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
TMP_DIR = Path("/tmp/ytmp3")
TMP_DIR.mkdir(exist_ok=True, parents=True)

REFRESH_INTERVAL = 600        # seconds between playlist refresh
RECHECK_INTERVAL = 1200       # seconds before checking MP3 needs update
CLEANUP_INTERVAL = 1800       # seconds between cleanup runs
EXPIRE_AGE = 7200             # seconds to keep old MP3 files

# Use playlist links directly
CHANNELS = {
    "dhruv": "https://www.youtube.com/playlist?list=PL4cUxeGkcC9jLYyp2Aoh6hcWuxFDX6PBJ"
}

VIDEO_CACHE = {name: {"videos": [], "last_checked": 0} for name in CHANNELS}

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
# Fetch playlist videos
# -----------------------------
def fetch_playlist_videos(name, playlist_url):
    cmd = [
        "yt-dlp",
        "--dump-single-json",
        "--flat-playlist",
        "--no-warnings",
        "--cookies-from-browser", "chrome",  # Automatically fetch cookies from Chrome
        playlist_url
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        entries = data.get("entries", [])
        videos = [{"id": e["id"], "url": f"https://www.youtube.com/watch?v={e['id']}"} for e in entries]
        return videos
    except Exception as e:
        logging.error(f"Failed to fetch playlist for {name}: {e}")
        return []

# -----------------------------
# Download & convert MP3
# -----------------------------
def download_and_convert(video_id, video_url):
    mp3_path = TMP_DIR / f"{video_id}.mp3"
    if mp3_path.exists():
        return mp3_path
    try:
        cmd = [
            "yt-dlp",
            "-f", "bestaudio",
            "--output", str(TMP_DIR / f"{video_id}.%(ext)s"),
            "--extract-audio",
            "--audio-format", "mp3",
            "--postprocessor-args", "ffmpeg:-ar 22050 -ac 1 -b:a 40k",
            "--no-warnings",
            "--cookies-from-browser", "chrome",
            video_url
        ]
        subprocess.run(cmd, check=True)
        if mp3_path.exists():
            return mp3_path
    except Exception as e:
        logging.warning(f"Failed to download {video_url}: {e}")
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
# Background updater
# -----------------------------
def update_playlist_loop():
    while True:
        for name, playlist_url in CHANNELS.items():
            videos = fetch_playlist_videos(name, playlist_url)
            VIDEO_CACHE[name]["videos"] = videos
            VIDEO_CACHE[name]["last_checked"] = time.time()
            # Pre-download latest video
            if videos:
                download_and_convert(videos[0]["id"], videos[0]["url"])
            time.sleep(random.randint(3, 7))
        time.sleep(REFRESH_INTERVAL)

# -----------------------------
# Flask Routes
# -----------------------------
@app.route("/<channel>.mp3")
def stream_mp3(channel):
    if channel not in CHANNELS:
        return "Channel not found", 404

    videos = VIDEO_CACHE[channel].get("videos", [])
    if not videos:
        return "No videos available", 500

    # Stream latest video in playlist
    video = videos[0]
    mp3_path = download_and_convert(video["id"], video["url"])
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
        videos = VIDEO_CACHE[channel].get("videos", [])
        status = "✔" if videos else "⏳"
        html += f'''
        <li>
            <a href="/{channel}.mp3">{channel}</a> {status}
        </li>
        '''
    html += "</ul>"
    return html

# -----------------------------
# Start background threads
# -----------------------------
threading.Thread(target=update_playlist_loop, daemon=True).start()
threading.Thread(target=cleanup_old_files, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
