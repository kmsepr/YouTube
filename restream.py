#!/usr/bin/env python3
import os
import time
import logging
import requests
import subprocess
from collections import deque
from flask import Flask, Response, render_template_string, abort, stream_with_context

# ============================================================
# Basic Setup
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

app = Flask(__name__)

REFRESH_INTERVAL = 1800  # 30 minutes
LOGO_FALLBACK = "https://iptv-org.github.io/assets/logo.png"

# iptv-org playlists
PLAYLISTS = {
    "all": "https://iptv-org.github.io/iptv/index.m3u",

    # Country
    "india": "https://iptv-org.github.io/iptv/countries/in.m3u",
    "usa": "https://iptv-org.github.io/iptv/countries/us.m3u",
    "uk": "https://iptv-org.github.io/iptv/countries/uk.m3u",
    "canada": "https://iptv-org.github.io/iptv/countries/ca.m3u",
    "australia": "https://iptv-org.github.io/iptv/countries/au.m3u",
    "germany": "https://iptv-org.github.io/iptv/countries/de.m3u",
    "france": "https://iptv-org.github.io/iptv/countries/fr.m3u",

    # Categories
    "news": "https://iptv-org.github.io/iptv/categories/news.m3u",
    "sports": "https://iptv-org.github.io/iptv/categories/sports.m3u",
    "entertainment": "https://iptv-org.github.io/iptv/categories/entertainment.m3u",
    "kids": "https://iptv-org.github.io/iptv/categories/kids.m3u",
    "movies": "https://iptv-org.github.io/iptv/categories/movies.m3u",
    "music": "https://iptv-org.github.io/iptv/categories/music.m3u",
    "documentary": "https://iptv-org.github.io/iptv/categories/documentary.m3u",
    "educational": "https://iptv-org.github.io/iptv/categories/educational.m3u",
    "religious": "https://iptv-org.github.io/iptv/categories/religious.m3u",
    "shopping": "https://iptv-org.github.io/iptv/categories/shopping.m3u",
    "regional": "https://iptv-org.github.io/iptv/categories/regional.m3u",
    "comedy": "https://iptv-org.github.io/iptv/categories/comedy.m3u",
    "lifestyle": "https://iptv-org.github.io/iptv/categories/lifestyle.m3u",
    "business": "https://iptv-org.github.io/iptv/categories/business.m3u",
    "travel": "https://iptv-org.github.io/iptv/categories/travel.m3u",
    "science": "https://iptv-org.github.io/iptv/categories/science.m3u",

    # Language
    "english": "https://iptv-org.github.io/iptv/languages/eng.m3u",
    "hindi": "https://iptv-org.github.io/iptv/languages/hin.m3u",
    "spanish": "https://iptv-org.github.io/iptv/languages/spa.m3u",
    "french": "https://iptv-org.github.io/iptv/languages/fra.m3u",
    "german": "https://iptv-org.github.io/iptv/languages/deu.m3u",
    "arabic": "https://iptv-org.github.io/iptv/languages/ara.m3u",
}

# Cache
CACHE = {}

# ============================================================
# M3U Parsing
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
        if eq == -1:
            break
        key_end = eq
        key_start = left.rfind(" ", 0, key_end)
        colon = left.rfind(":", 0, key_end)
        if colon > key_start:
            key_start = colon
        key = left[key_start + 1:key_end].strip()

        if eq + 1 < len(left) and left[eq + 1] == '"':
            val_start = eq + 2
            val_end = left.find('"', val_start)
            if val_end == -1:
                break
            val = left[val_start:val_end]
            pos = val_end + 1
        else:
            val_end = left.find(" ", eq + 1)
            if val_end == -1:
                val_end = len(left)
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
                channels.append(
                    {
                        "title": title or attrs.get("tvg-name") or "Unknown",
                        "url": url,
                        "logo": attrs.get("tvg-logo") or "",
                        "group": attrs.get("group-title") or "",
                        "tvg_id": attrs.get("tvg-id") or "",
                    }
                )
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

    url = PLAYLISTS[name]
    logging.info("[%s] Fetching playlist: %s", name, url)
    try:
        resp = requests.get(url, timeout=25)
        resp.raise_for_status()
        channels = parse_m3u(resp.text)
        CACHE[name] = {"time": now, "channels": channels}
        logging.info("[%s] Loaded %d channels", name, len(channels))
        return channels
    except Exception as e:
        logging.error("Failed to load playlist %s: %s", name, e)
        return []

# ============================================================
# AUDIO-ONLY (still proxied)
# ============================================================
def proxy_audio_only(source_url: str):
    cmd = [
        "ffmpeg",
        "-loglevel", "error",
        "-i", source_url,
        "-vn",
        "-ac", "1",
        "-ar", "44100",
        "-b:a", "40k",
        "-f", "mp3",
        "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while True:
            data = proc.stdout.read(64 * 1024)
            if not data:
                break
            yield data
    finally:
        try:
            proc.terminate()
            time.sleep(0.5)
            if proc.poll() is None:
                proc.kill()
        except:
            pass

# ============================================================
# HTML TEMPLATES
# ============================================================
HOME_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IPTV Restream</title>
<style>
body{background:#000;color:#0f0;font-family:Arial;margin:0;padding:16px}
a{color:#0f0;text-decoration:none;border:1px solid #0f0;padding:10px;margin:8px;border-radius:8px;display:inline-block}
a:hover{background:#0f0;color:#000}
</style>
</head>
<body>
<h2>📺 IPTV Restream (Raw m3u8 mode)</h2>
<p>Select a category:</p>

{% for key, url in playlists.items() %}
<a href="/list/{{ key }}">{{ key|capitalize }}</a>
{% endfor %}

</body>
</html>"""

LIST_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ group|capitalize }} Channels</title>
<style>
body{background:#000;color:#0f0;font-family:Arial;margin:0;padding:16px}
.card{display:flex;align-items:center;gap:10px;border:1px solid #0f0;border-radius:8px;padding:8px;margin:8px 0;background:#111}
.card img{width:42px;height:42px;object-fit:contain;background:#222;border-radius:6px}
a.btn{border:1px solid #0f0;color:#0f0;padding:6px 8px;border-radius:6px;text-decoration:none;margin-right:10px}
a.btn:hover{background:#0f0;color:#000}
</style>
</head>
<body>
<h3>{{ group|capitalize }} Channels</h3>
<a href="/">← Back</a>

{% for ch in channels %}
<div class="card">
  <img src="{{ ch.logo or fallback }}" onerror="this.src='{{ fallback }}'">
  <div style="flex:1">
    <strong>{{ loop.index0 }}.</strong> {{ ch.title }}
    <div>
      <a class="btn" href="/watch/{{ group }}/{{ loop.index0 }}" target="_blank">▶ Watch</a>
      <a class="btn" href="/play-audio/{{ group }}/{{ loop.index0 }}" target="_blank">🎧 Audio only</a>
    </div>
  </div>
</div>
{% endfor %}
</body>
</html>"""

WATCH_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ channel.title }}</title>
<style>
body{margin:0;padding:0;background:#000;color:#0f0}
video{width:100%;height:auto;max-height:90vh;border:2px solid #0f0;margin-top:10px}
</style>
</head>
<body>

<h3 style="text-align:center">{{ channel.title }}</h3>

<video id="vid" controls autoplay playsinline>
    <source src="{{ channel.url }}" type="{{ mime_type }}">
</video>

<script>
// Optional autoplay recovery
document.getElementById("vid").addEventListener("error", () => {
    alert("Video could not play. Stream may be offline.");
});
</script>

</body>
</html>
"""

# ============================================================
# ROUTES
# ============================================================
@app.route("/")
def home():
    return render_template_string(HOME_HTML, playlists=PLAYLISTS)

@app.route("/list/<group>")
def list_group(group):
    if group not in PLAYLISTS:
        abort(404)
    return render_template_string(
        LIST_HTML,
        group=group,
        channels=get_channels(group),
        fallback=LOGO_FALLBACK
    )

@app.route("/watch/<group>/<int:idx>")
def watch_channel(group, idx):
    if group not in PLAYLISTS:
        abort(404)
    channels = get_channels(group)
    if idx < 0 or idx >= len(channels):
        abort(404)

    ch = channels[idx]

    # Browser raw playback detection
    if ".m3u8" in ch["url"]:
        mime = "application/vnd.apple.mpegurl"
    elif ".mp4" in ch["url"]:
        mime = "video/mp4"
    elif ".webm" in ch["url"]:
        mime = "video/webm"
    else:
        mime = "video/mp2t"

    return render_template_string(
        WATCH_HTML,
        channel=ch,
        group=group,
        idx=idx,
        mime_type=mime
    )

@app.route("/play-audio/<group>/<int:idx>")
def play_channel_audio(group, idx):
    if group not in PLAYLISTS:
        abort(404)
    channels = get_channels(group)
    if idx < 0 or idx >= len(channels):
        abort(404)

    ch = channels[idx]

    def gen():
        for chunk in proxy_audio_only(ch["url"]):
            yield chunk

    headers = {
        "Content-Disposition": f'inline; filename="{group}_{idx}.mp3"',
        'Access-Control-Allow-Origin': '*',
    }

    return Response(stream_with_context(gen()), mimetype="audio/mpeg", headers=headers)

# ============================================================
# Entry
# ============================================================
if __name__ == "__main__":
    print("Running IPTV server (RAW m3u8 mode) on http://0.0.0.0:8000")
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)