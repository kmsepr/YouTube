#!/usr/bin/env python3
import os
import time
import logging
import random
import requests
import subprocess
import tempfile
import threading
import shutil
from flask import Flask, Response, render_template_string, abort, stream_with_context, request, send_from_directory

# ============================================================
# Basic Setup
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

app = Flask(__name__)

REFRESH_INTERVAL = 1800
LOGO_FALLBACK = "https://iptv-org.github.io/assets/logo.png"

# ============================================================
# PLAYLISTS (QUALITY REMOVED) - UPDATED WITH MANY LANGUAGES
# ============================================================
PLAYLISTS = {
    "all": "https://iptv-org.github.io/iptv/index.m3u",
    "india": "https://iptv-org.github.io/iptv/countries/in.m3u",
    "usa": "https://iptv-org.github.io/iptv/countries/us.m3u",
    "uk": "https://iptv-org.github.io/iptv/countries/uk.m3u",
    "uae": "https://iptv-org.github.io/iptv/countries/ae.m3u",
    "saudi": "https://iptv-org.github.io/iptv/countries/sa.m3u",
    "pakistan": "https://iptv-org.github.io/iptv/countries/pk.m3u",
    "news": "https://iptv-org.github.io/iptv/categories/news.m3u",
    "sports": "https://iptv-org.github.io/iptv/categories/sports.m3u",
    "movies": "https://iptv-org.github.io/iptv/categories/movies.m3u",
    "music": "https://iptv-org.github.io/iptv/categories/music.m3u",
    "kids": "https://iptv-org.github.io/iptv/categories/kids.m3u",
    "entertainment": "https://iptv-org.github.io/iptv/categories/entertainment.m3u",
    "english": "https://iptv-org.github.io/iptv/languages/eng.m3u",
    "hindi": "https://iptv-org.github.io/iptv/languages/hin.m3u",
    "tamil": "https://iptv-org.github.io/iptv/languages/tam.m3u",
    "telugu": "https://iptv-org.github.io/iptv/languages/tel.m3u",
    "malayalam": "https://iptv-org.github.io/iptv/languages/mal.m3u",
    "kannada": "https://iptv-org.github.io/iptv/languages/kan.m3u",
    "marathi": "https://iptv-org.github.io/iptv/languages/mar.m3u",
    "gujarati": "https://iptv-org.github.io/iptv/languages/guj.m3u",
    "bengali": "https://iptv-org.github.io/iptv/languages/ben.m3u",
    "punjabi": "https://iptv-org.github.io/iptv/languages/pan.m3u",
    "arabic": "https://iptv-org.github.io/iptv/languages/ara.m3u",
    "urdu": "https://iptv-org.github.io/iptv/languages/urd.m3u",
    "french": "https://iptv-org.github.io/iptv/languages/fra.m3u",
    "spanish": "https://iptv-org.github.io/iptv/languages/spa.m3u",
    "german": "https://iptv-org.github.io/iptv/languages/deu.m3u",
    "turkish": "https://iptv-org.github.io/iptv/languages/tur.m3u",
    "russian": "https://iptv-org.github.io/iptv/languages/rus.m3u",
    "chinese": "https://iptv-org.github.io/iptv/languages/zho.m3u",
    "japanese": "https://iptv-org.github.io/iptv/languages/jpn.m3u",
    "korean": "https://iptv-org.github.io/iptv/languages/kor.m3u",
}

CACHE = {}
LOW_TEMP_FOLDER = tempfile.gettempdir()
LOW_MAX_AGE = 30 * 60  # 30 minutes

# ============================================================
# M3U PARSER
# ============================================================
def parse_extinf(line: str):
    if "," in line:
        left, title = line.split(",", 1)
    else:
        left, title = line, ""
    attrs = {}
    pos = 0
    while True:
        eq = left.find("=", pos)
        if eq == -1: break
        key_end = eq
        key_start = left.rfind(" ", 0, key_end)
        colon = left.rfind(":", 0, key_end)
        if colon > key_start: key_start = colon
        key = left[key_start + 1:key_end].strip()
        if eq + 1 < len(left) and left[eq + 1] == '"':
            val_start = eq + 2
            val_end = left.find('"', val_start)
            if val_end == -1: break
            val = left[val_start:val_end]
            pos = val_end + 1
        else:
            val_end = left.find(" ", eq + 1)
            if val_end == -1: val_end = len(left)
            val = left[eq + 1:val_end].strip()
            pos = val_end
        attrs[key] = val
    return attrs, title.strip()

def parse_m3u(text: str):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    channels = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            attrs, title = parse_extinf(lines[i])
            j = i + 1
            url = None
            while j < len(lines):
                if not lines[j].startswith("#"):
                    url = lines[j]
                    break
                j += 1
            if url:
                channels.append({
                    "title": title or attrs.get("tvg-name") or "Unknown",
                    "url": url,
                    "logo": attrs.get("tvg-logo") or "",
                    "group": attrs.get("group-title") or "",
                    "tvg_id": attrs.get("tvg-id") or "",
                })
            i = j + 1
        else:
            i += 1
    return channels

# ============================================================
# Cache Loader
# ============================================================
def get_channels(name: str):
    now = time.time()
    cached = CACHE.get(name)
    if cached and now - cached.get("time", 0) < REFRESH_INTERVAL:
        return cached["channels"]
    url = PLAYLISTS.get(name)
    if not url: return []
    try:
        resp = requests.get(url, timeout=25)
        resp.raise_for_status()
        channels = parse_m3u(resp.text)
        CACHE[name] = {"time": now, "channels": channels}
        return channels
    except Exception as e:
        logging.error("Load failed %s: %s", name, e)
        return []

# ============================================================
# Audio-only proxy
# ============================================================
def proxy_audio_only(source_url: str):
    cmd = [
        "ffmpeg", "-loglevel", "error", "-i", source_url,
        "-vn", "-ac", "1", "-ar", "44100", "-b:a", "40k",
        "-f", "mp3", "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    try:
        while True:
            data = proc.stdout.read(64*1024)
            if not data: break
            yield data
    finally:
        try: proc.terminate(); time.sleep(0.5); proc.kill()
        except: pass

# ============================================================
# Low-quality HLS 144p
# ============================================================
def get_low_folder(group, idx):
    folder = os.path.join(LOW_TEMP_FOLDER, f"low_{group}_{idx}")
    os.makedirs(folder, exist_ok=True)
    return folder

def transcode_144p(source_url, folder):
    hls_path = os.path.join(folder, "index.m3u8")
    if os.path.exists(hls_path): return hls_path
    cmd = [
        "ffmpeg", "-i", source_url,
        "-vf", "scale=256:144",
        "-c:v", "libx264", "-preset", "veryfast", "-b:v", "150k",
        "-c:a", "aac", "-b:a", "48k",
        "-f", "hls",
        "-hls_time", "6", "-hls_list_size", "5", "-hls_flags", "delete_segments",
        hls_path
    ]
    subprocess.Popen(cmd)
    return hls_path

@app.route("/low/<group>/<int:idx>/<path:filename>")
def serve_low(group, idx, filename):
    folder = get_low_folder(group, idx)
    file_path = os.path.join(folder, filename)
    if not os.path.exists(file_path): abort(404)
    return send_from_directory(folder, filename)

def cleanup_old_low():
    while True:
        now = time.time()
        for name in os.listdir(LOW_TEMP_FOLDER):
            if not name.startswith("low_"): continue
            path = os.path.join(LOW_TEMP_FOLDER, name)
            try:
                mtime = os.path.getmtime(path)
                if now - mtime > LOW_MAX_AGE:
                    shutil.rmtree(path, ignore_errors=True)
                    logging.info("Removed old low folder: %s", path)
            except Exception as e:
                logging.error("Cleanup error: %s", e)
        time.sleep(600)  # every 10 minutes

threading.Thread(target=cleanup_old_low, daemon=True).start()

# ============================================================
# Flask routes (watch, low, audio, favourites, search)
# ============================================================
@app.route("/")
def home():
    return render_template_string(HOME_HTML, playlists=PLAYLISTS)

@app.route("/list/<group>")
def list_group(group):
    channels = get_channels(group)
    return render_template_string(LIST_HTML, group=group, channels=channels, fallback=LOGO_FALLBACK)

@app.route("/watch/<group>/<int:idx>")
def watch_channel(group, idx):
    channels = get_channels(group)
    if idx < 0 or idx >= len(channels): abort(404)
    ch = channels[idx]
    mime = "application/vnd.apple.mpegurl" if ".m3u8" in ch["url"] else "video/mp4"
    return render_template_string(WATCH_HTML, channel=ch, mime_type=mime)

@app.route("/watch-low/<group>/<int:idx>")
def watch_low(group, idx):
    channels = get_channels(group)
    if idx < 0 or idx >= len(channels): abort(404)
    ch = channels[idx]
    folder = get_low_folder(group, idx)
    transcode_144p(ch["url"], folder)
    low_url = f"/low/{group}/{idx}/index.m3u8"
    channel = dict(ch); channel["url"] = low_url
    return render_template_string(WATCH_HTML, channel=channel, mime_type="application/vnd.apple.mpegurl")

@app.route("/play-audio/<group>/<int:idx>")
def play_channel_audio(group, idx):
    channels = get_channels(group)
    if idx < 0 or idx >= len(channels): abort(404)
    ch = channels[idx]
    return Response(stream_with_context(proxy_audio_only(ch["url"])),
                    mimetype="audio/mpeg", headers={"Access-Control-Allow-Origin":"*"})

# --- Keep all previous HTML templates and JS intact

# ============================================================
# Entry
# ============================================================
if __name__ == "__main__":
    logging.info("Running IPTV Restream on http://0.0.0.0:8000")
    app.run(host="0.0.0.0", port=8000, threaded=True)