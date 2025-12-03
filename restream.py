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

# iptv-org playlists - comprehensive list of categories [web:1][web:4][web:5]
PLAYLISTS = {
    # Main playlists
    "all": "https://iptv-org.github.io/iptv/index.m3u",  # all channels [web:1]
    
    # Country-specific
    "india": "https://iptv-org.github.io/iptv/countries/in.m3u",   # India
    "usa": "https://iptv-org.github.io/iptv/countries/us.m3u",     # United States
    "uk": "https://iptv-org.github.io/iptv/countries/uk.m3u",      # United Kingdom
    "canada": "https://iptv-org.github.io/iptv/countries/ca.m3u",  # Canada
    "australia": "https://iptv-org.github.io/iptv/countries/au.m3u", # Australia
    "germany": "https://iptv-org.github.io/iptv/countries/de.m3u", # Germany
    "france": "https://iptv-org.github.io/iptv/countries/fr.m3u",  # France
    
    # Categories
    "news": "https://iptv-org.github.io/iptv/categories/news.m3u",  # News [web:4]
    "sports": "https://iptv-org.github.io/iptv/categories/sports.m3u",  # Sports
    "entertainment": "https://iptv-org.github.io/iptv/categories/entertainment.m3u",  # Entertainment
    "kids": "https://iptv-org.github.io/iptv/categories/kids.m3u",  # Kids
    "movies": "https://iptv-org.github.io/iptv/categories/movies.m3u",  # Movies
    "music": "https://iptv-org.github.io/iptv/categories/music.m3u",  # Music
    "documentary": "https://iptv-org.github.io/iptv/categories/documentary.m3u",  # Documentary
    "educational": "https://iptv-org.github.io/iptv/categories/educational.m3u",  # Educational
    "religious": "https://iptv-org.github.io/iptv/categories/religious.m3u",  # Religious
    "shopping": "https://iptv-org.github.io/iptv/categories/shopping.m3u",  # Shopping
    
    # Language-based
    "english": "https://iptv-org.github.io/iptv/languages/eng.m3u",  # English
    "hindi": "https://iptv-org.github.io/iptv/languages/hin.m3u",  # Hindi
    "spanish": "https://iptv-org.github.io/iptv/languages/spa.m3u",  # Spanish
    "french": "https://iptv-org.github.io/iptv/languages/fra.m3u",  # French
    "german": "https://iptv-org.github.io/iptv/languages/deu.m3u",  # German
    "arabic": "https://iptv-org.github.io/iptv/languages/ara.m3u",  # Arabic
    
    # Special categories
    "regional": "https://iptv-org.github.io/iptv/categories/regional.m3u",  # Regional
    "comedy": "https://iptv-org.github.io/iptv/categories/comedy.m3u",  # Comedy
    "lifestyle": "https://iptv-org.github.io/iptv/categories/lifestyle.m3u",  # Lifestyle
    "business": "https://iptv-org.github.io/iptv/categories/business.m3u",  # Business
    "travel": "https://iptv-org.github.io/iptv/categories/travel.m3u",  # Travel
    "science": "https://iptv-org.github.io/iptv/categories/science.m3u",  # Science & Technology
}

# Cache: { name: { "time": ts, "channels": [...] } }
CACHE = {}

# ============================================================
# M3U Parsing
# ============================================================
def parse_extinf(line: str):
    """
    Parse an #EXTINF line of extended M3U.
    Example: #EXTINF:-1 tvg-id="X" tvg-logo="Y" group-title="News", Channel Name
    Returns: (attrs: dict, title: str)
    """
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
    """
    Parse M3U content into list of channels.
    Each channel: {title, url, logo, group, tvg_id}
    M3U format is widely used for IPTV, with EXTM3U/EXTINF tags defining streams. [web:23][web:28]
    """
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
# Cache + Loader
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
        # Return empty list if playlist fails to load
        return []

# ============================================================
# Streaming Helpers
# ============================================================
def proxy_stream(source_url: str):
    """
    Raw proxy of IPTV stream.
    M3U entries usually point to HTTP/HLS URLs, which can be re-streamed by reading chunks. [web:28]
    """
    with requests.get(source_url, stream=True, timeout=25) as r:
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if not chunk:
                break
            yield chunk

def proxy_audio_only(source_url: str):
    """
    Audio-only restream using ffmpeg:
      -vn (no video), mono, 44.1kHz, 64 kbps MP3.
    ffmpeg can transcode network inputs directly to mp3 on stdout. [web:38]
    """
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
    except Exception as e:
        logging.error("Error in audio stream: %s", e)
    finally:
        try:
            proc.terminate()
            # Wait a bit for graceful termination
            time.sleep(0.5)
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        except Exception as e:
            logging.error("Error terminating ffmpeg process: %s", e)

# ============================================================
# HTML TEMPLATES
# ============================================================
HOME_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IPTV Restream</title>
<style>
body{background:#000;color:#0f0;font-family:Arial,Helvetica,sans-serif;margin:0;padding:16px}
a{color:#0f0;text-decoration:none;border:1px solid #0f0;padding:10px;margin:8px;border-radius:8px;display:inline-block}
a:hover{background:#0f0;color:#000}
h2{margin-top:4px}
.category-group{margin-bottom:24px}
.category-title{color:#0ff;font-size:18px;margin-bottom:12px;border-bottom:1px solid #0ff;padding-bottom:4px}
.category-title::before{content:"📺 ";margin-right:8px}
</style>
</head>
<body>
<h2>📺 IPTV Restream</h2>
<p>Select a category:</p>

<div class="category-group">
    <div class="category-title">All Channels</div>
    <a href="/list/all">🌍 All Channels</a>
</div>

<div class="category-group">
    <div class="category-title">Categories</div>
    <a href="/list/news">📰 News</a>
    <a href="/list/sports">⚽ Sports</a>
    <a href="/list/entertainment">🎭 Entertainment</a>
    <a href="/list/kids">🧒 Kids</a>
    <a href="/list/movies">🎬 Movies</a>
    <a href="/list/music">🎵 Music</a>
    <a href="/list/documentary">📽️ Documentary</a>
    <a href="/list/comedy">😂 Comedy</a>
    <a href="/list/lifestyle">🏠 Lifestyle</a>
    <a href="/list/business">💼 Business</a>
    <a href="/list/travel">✈️ Travel</a>
    <a href="/list/science">🔬 Science & Tech</a>
    <a href="/list/educational">📚 Educational</a>
    <a href="/list/religious">🙏 Religious</a>
    <a href="/list/shopping">🛒 Shopping</a>
    <a href="/list/regional">📍 Regional</a>
</div>

<div class="category-group">
    <div class="category-title">Countries</div>
    <a href="/list/india">🇮🇳 India</a>
    <a href="/list/usa">🇺🇸 USA</a>
    <a href="/list/uk">🇬🇧 UK</a>
    <a href="/list/canada">🇨🇦 Canada</a>
    <a href="/list/australia">🇦🇺 Australia</a>
    <a href="/list/germany">🇩🇪 Germany</a>
    <a href="/list/france">🇫🇷 France</a>
</div>

<div class="category-group">
    <div class="category-title">Languages</div>
    <a href="/list/english">🇬🇧 English</a>
    <a href="/list/hindi">🇮🇳 Hindi</a>
    <a href="/list/spanish">🇪🇸 Spanish</a>
    <a href="/list/french">🇫🇷 French</a>
    <a href="/list/german">🇩🇪 German</a>
    <a href="/list/arabic">🇸🇦 Arabic</a>
</div>

<p style="margin-top:24px;opacity:.7;font-size:13px;border-top:1px solid #333;padding-top:12px">
Using public iptv-org playlists. Channels may go offline or change URLs.
</p>
</body>
</html>"""

LIST_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ group|capitalize }} Channels</title>
<style>
body{background:#000;color:#0f0;font-family:Arial,Helvetica,sans-serif;margin:0;padding:16px}
a{color:#0f0}
.card{display:flex;align-items:center;gap:10px;border:1px solid #0f0;border-radius:8px;padding:8px;margin:8px 0;background:#111}
.card img{width:42px;height:42px;object-fit:contain;background:#222;border-radius:6px}
.btns a{border:1px solid #0f0;padding:6px 8px;border-radius:6px;margin-right:8px;display:inline-block;text-decoration:none}
.btns a:hover{background:#0f0;color:#000}
.meta{opacity:.8;font-size:12px}
.channel-count{opacity:.7;font-size:14px;margin-bottom:16px}
.empty-message{color:#f00;padding:20px;text-align:center;border:1px solid #f00;border-radius:8px}
</style>
</head>
<body>
<h3>Category: {{ group|capitalize }}</h3>
<p><a href="/">← Back to Categories</a></p>

{% if channels %}
<div class="channel-count">{{ channels|length }} channels found</div>
{% for ch in channels %}
<div class="card">
  <img src="{{ ch.logo or fallback }}" alt="logo" onerror="this.src='{{ fallback }}'">
  <div style="flex:1">
    <div><strong>{{ loop.index0 }}.</strong> {{ ch.title }}</div>
    <div class="meta">{{ ch.group }}{% if ch.tvg_id %} · {{ ch.tvg_id }}{% endif %}</div>
    <div class="btns">
      <a href="/play/{{ group }}/{{ loop.index0 }}" target="_blank">▶ Video</a>
      <a href="/play-audio/{{ group }}/{{ loop.index0 }}" target="_blank">🎧 Audio only</a>
    </div>
  </div>
</div>
{% endfor %}
{% else %}
<div class="empty-message">
  No channels available for this category.<br>
  The playlist might be temporarily unavailable or empty.
</div>
{% endif %}
</body>
</html>"""

# ============================================================
# Routes
# ============================================================
@app.route("/")
def home():
    return render_template_string(HOME_HTML)

@app.route("/list/<group>")
def list_group(group):
    if group not in PLAYLISTS:
        abort(404)
    try:
        channels = get_channels(group)
    except Exception as e:
        logging.exception("Error loading channels for %s", group)
        abort(502)
    return render_template_string(LIST_HTML, group=group, channels=channels, fallback=LOGO_FALLBACK)

@app.route("/play/<group>/<int:idx>")
def play_channel(group, idx):
    if group not in PLAYLISTS:
        abort(404)
    channels = get_channels(group)
    if idx < 0 or idx >= len(channels):
        abort(404)
    ch = channels[idx]

    def gen():
        try:
            for chunk in proxy_stream(ch["url"]):
                yield chunk
        except Exception as e:
            logging.error("Error streaming video: %s", e)

    mimetype = "video/mp2t"
    if ".m3u8" in ch["url"]:
        mimetype = "application/vnd.apple.mpegurl"
    return Response(stream_with_context(gen()), mimetype=mimetype)

@app.route("/play-audio/<group>/<int:idx>")
def play_channel_audio(group, idx):
    if group not in PLAYLISTS:
        abort(404)
    channels = get_channels(group)
    if idx < 0 or idx >= len(channels):
        abort(404)
    ch = channels[idx]

    def gen():
        try:
            for chunk in proxy_audio_only(ch["url"]):
                yield chunk
        except Exception as e:
            logging.error("Error streaming audio: %s", e)

    headers = {"Content-Disposition": f'inline; filename="{group}_{idx}.mp3"'}
    return Response(stream_with_context(gen()), mimetype="audio/mpeg", headers=headers)

# ============================================================
# Entry
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("IPTV Restream Server")
    print("=" * 60)
    print(f"Available categories: {len(PLAYLISTS)}")
    print("Main Categories: All, News, Sports, Entertainment, Kids, Movies, Music")
    print("Countries: India, USA, UK, Canada, Australia, Germany, France")
    print("Languages: English, Hindi, Spanish, French, German, Arabic")
    print("=" * 60)
    print("Starting server on http://0.0.0.0:8000")
    print("=" * 60)
    
    app.run(host="0.0.0.0", port=8000, debug=False)