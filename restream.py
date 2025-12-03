#!/usr/bin/env python3
import os
import time
import logging
import requests
import subprocess
import re
from collections import deque
from flask import Flask, Response, render_template_string, abort, stream_with_context, request, redirect

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

# iptv-org playlists - comprehensive list of categories
PLAYLISTS = {
    "all": "https://iptv-org.github.io/iptv/index.m3u",
    "india": "https://iptv-org.github.io/iptv/countries/in.m3u",
    "usa": "https://iptv-org.github.io/iptv/countries/us.m3u",
    "uk": "https://iptv-org.github.io/iptv/countries/uk.m3u",
    "canada": "https://iptv-org.github.io/iptv/countries/ca.m3u",
    "australia": "https://iptv-org.github.io/iptv/countries/au.m3u",
    "germany": "https://iptv-org.github.io/iptv/countries/de.m3u",
    "france": "https://iptv-org.github.io/iptv/countries/fr.m3u",
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
    "english": "https://iptv-org.github.io/iptv/languages/eng.m3u",
    "hindi": "https://iptv-org.github.io/iptv/languages/hin.m3u",
    "spanish": "https://iptv-org.github.io/iptv/languages/spa.m3u",
    "french": "https://iptv-org.github.io/iptv/languages/fra.m3u",
    "german": "https://iptv-org.github.io/iptv/languages/deu.m3u",
    "arabic": "https://iptv-org.github.io/iptv/languages/ara.m3u",
    "regional": "https://iptv-org.github.io/iptv/categories/regional.m3u",
    "comedy": "https://iptv-org.github.io/iptv/categories/comedy.m3u",
    "lifestyle": "https://iptv-org.github.io/iptv/categories/lifestyle.m3u",
    "business": "https://iptv-org.github.io/iptv/categories/business.m3u",
    "travel": "https://iptv-org.github.io/iptv/categories/travel.m3u",
    "science": "https://iptv-org.github.io/iptv/categories/science.m3u",
}

# Cache: { name: { "time": ts, "channels": [...] } }
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
        return []

# ============================================================
# HLS/M3U8 Proxy for Browser Playback
# ============================================================
def rewrite_m3u8_playlist(m3u8_url: str, base_url: str):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'http://localhost:8000/',
    }

    response = requests.get(m3u8_url, headers=headers, timeout=25)
    response.raise_for_status()

    content = response.text
    content_type = response.headers.get('content-type', 'application/vnd.apple.mpegurl')

    lines = content.split('\n')
    rewritten_lines = []

    if base_url.endswith('.m3u8'):
        base_url = base_url.rsplit('/', 1)[0]

    rewritten_lines.append('#EXTM3U')
    rewritten_lines.append('#EXT-X-VERSION:3')
    rewritten_lines.append('#EXT-X-ALLOW-CACHE:NO')
    rewritten_lines.append('#EXT-X-PLAYLIST-TYPE:VOD' if '#EXT-X-ENDLIST' in content else '#EXT-X-PLAYLIST-TYPE:LIVE')

    for line in lines:
        stripped_line = line.strip()

        if stripped_line.startswith('#EXTM3U'):
            continue
        if stripped_line.startswith('#EXT-X-VERSION'):
            continue
        if stripped_line.startswith('#EXT-X-ALLOW-CACHE'):
            continue
        if stripped_line.startswith('#EXT-X-PLAYLIST-TYPE'):
            continue

        if not stripped_line.startswith('#') and stripped_line and not stripped_line.startswith('http'):
            if stripped_line.startswith('/'):
                parsed_url = requests.utils.urlparse(m3u8_url)
                absolute_url = f"{parsed_url.scheme}://{parsed_url.netloc}{stripped_line}"
            else:
                if not base_url.endswith('/'):
                    base_url += '/'
                absolute_url = base_url + stripped_line
            rewritten_lines.append(f"/proxy-segment/{requests.utils.quote(absolute_url, safe='')}")
        elif stripped_line.startswith('http'):
            rewritten_lines.append(f"/proxy-segment/{requests.utils.quote(stripped_line, safe='')}")
        else:
            rewritten_lines.append(line)

    return '\n'.join(rewritten_lines), content_type

def proxy_m3u8(m3u8_url: str):
    try:
        rewritten_content, content_type = rewrite_m3u8_playlist(m3u8_url, m3u8_url)
        return rewritten_content, content_type
    except Exception as e:
        logging.error("Error rewriting M3U8 playlist %s: %s", m3u8_url, e)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
            'Referer': 'http://localhost:8000/',
        }
        response = requests.get(m3u8_url, headers=headers, timeout=25)
        response.raise_for_status()
        return response.text, response.headers.get('content-type', 'application/vnd.apple.mpegurl')

def proxy_segment(segment_url: str):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': '*/*',
        'Referer': 'http://localhost:8000/',
        'Origin': 'http://localhost:8000',
    }

    # stream upstream response and yield chunks; include CORS headers on our response
    response = requests.get(segment_url, headers=headers, stream=True, timeout=25)
    response.raise_for_status()

    # derive content type if possible
    content_type = response.headers.get('content-type', 'video/MP2T')
    if '.ts' in segment_url or segment_url.endswith('.ts'):
        content_type = 'video/MP2T'
    elif '.m4s' in segment_url or segment_url.endswith('.m4s'):
        content_type = 'video/iso.segment'

    def generate():
        try:
            for chunk in response.iter_content(chunk_size=64*1024):
                if not chunk:
                    continue
                yield chunk
        finally:
            try:
                response.close()
            except Exception:
                pass

    # Important: include CORS headers so browser can fetch segments
    headers_out = {
        'Content-Type': content_type,
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
    }

    return Response(stream_with_context(generate()), headers=headers_out)

def proxy_direct_stream(source_url: str):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'http://localhost:8000/',
        'Origin': 'http://localhost:8000',
    }

    response = requests.get(source_url, headers=headers, stream=True, timeout=25)
    response.raise_for_status()

    content_type = response.headers.get('content-type', 'video/mp2t')
    if '.mp4' in source_url or source_url.endswith('.mp4'):
        content_type = 'video/mp4'
    elif '.webm' in source_url or source_url.endswith('.webm'):
        content_type = 'video/webm'

    def generate():
        try:
            for chunk in response.iter_content(chunk_size=64*1024):
                if not chunk:
                    continue
                yield chunk
        finally:
            try:
                response.close()
            except Exception:
                pass

    return content_type, generate

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
    except Exception as e:
        logging.error("Error in audio stream: %s", e)
    finally:
        try:
            proc.terminate()
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
      <a href="/watch/{{ group }}/{{ loop.index0 }}" target="_blank">▶ Watch Video</a>
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

# autoplay-friendly WATCH_HTML (muted autoplay, hls.js, overlay, unmute)
WATCH_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Watch: {{ channel.title }}</title>
<style>
body { margin: 0; padding: 0; background: #000; color: #0f0; font-family: Arial, sans-serif; }
.container { max-width: 1200px; margin: 0 auto; padding: 20px; }
.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.back-btn { display: inline-block; padding: 10px 20px; background: #0f0; color: #000; text-decoration: none; border-radius: 5px; border: none; cursor: pointer; }
.back-btn:hover { background: #0c0; }
h1 { margin: 0; color: #0ff; }
.channel-info { background: #111; padding: 15px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #0f0; }
.stream-container { background: #000; border: 2px solid #0f0; border-radius: 8px; overflow: hidden; position:relative; }
#videoPlayer { width: 100%; height: 70vh; background: #000; display:block; }
.stream-status { padding: 15px; background: #111; border-top: 1px solid #0f0; }
.status-online { color: #0f0; }
.status-offline { color: #f00; }
.stream-url { word-break: break-all; font-size: 12px; opacity: 0.7; margin-top: 10px; }
.unmute-btn { position: absolute; right: 12px; bottom: 12px; z-index: 20; background: rgba(0,0,0,0.6); color: #0f0; border: 1px solid #0f0; padding: 8px 10px; border-radius: 6px; cursor: pointer; font-size: 14px; backdrop-filter: blur(4px); }
.unmute-btn.hidden { display: none; }
.overlay-play { position:absolute; left:50%; top:50%; transform:translate(-50%,-50%); background:rgba(0,0,0,0.7); color:#0f0; border:1px solid #0f0; padding:12px 18px; border-radius:8px; cursor:pointer; display:none; }
.overlay-play.visible { display:block; }
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>📺 {{ channel.title }}</h1>
        <a href="/list/{{ group }}" class="back-btn">← Back to List</a>
    </div>
    
    <div class="channel-info">
        <div><strong>Group:</strong> {{ channel.group }}</div>
        {% if channel.tvg_id %}<div><strong>TVG ID:</strong> {{ channel.tvg_id }}</div>{% endif %}
        <div class="stream-url"><strong>Stream URL:</strong> {{ channel.url }}</div>
    </div>
    
    <div class="stream-container">
        <video id="videoPlayer" controls autoplay muted playsinline>
            <source id="videoSource" src="/play/{{ group }}/{{ idx }}" type="application/vnd.apple.mpegurl">
            Your browser does not support HLS streaming.
        </video>

        <button id="unmuteBtn" class="unmute-btn hidden" title="Unmute">🔊 Unmute</button>
        <div id="overlayPlay" class="overlay-play">Click to start</div>
    </div>
    
    <div class="stream-status">
        <div id="status" class="status-online">● Streaming</div>
        <div style="margin-top: 10px; font-size: 14px;">
            <strong>Tip:</strong> If the stream doesn't play, try:
            <ul style="margin: 5px 0; padding-left: 20px;">
                <li>Refreshing the page</li>
                <li>Using Chrome or Edge browser</li>
                <li>Checking if the channel is currently broadcasting</li>
            </ul>
        </div>
    </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.1/dist/hls.min.js"></script>

<script>
const videoPlayer = document.getElementById('videoPlayer');
const statusElement = document.getElementById('status');
const unmuteBtn = document.getElementById('unmuteBtn');
const overlayPlay = document.getElementById('overlayPlay');
const src = document.getElementById('videoSource').getAttribute('src');

function showOverlay() {
    overlayPlay.classList.add('visible');
}

function initHls() {
    if (window.Hls && Hls.isSupported()) {
        const hls = new Hls({ maxBufferLength: 30 });
        hls.attachMedia(videoPlayer);
        hls.on(Hls.Events.MEDIA_ATTACHED, function () {
            hls.loadSource(src);
        });
        hls.on(Hls.Events.ERROR, function (event, data) {
            console.warn('hls.js error', data);
            if (data.fatal) {
                statusElement.textContent = '● Stream error (hls.js)'; 
                statusElement.className = 'status-offline';
            }
        });
    } else if (videoPlayer.canPlayType('application/vnd.apple.mpegurl')) {
        videoPlayer.src = src;
    } else {
        statusElement.textContent = '● HLS not supported in this browser';
        statusElement.className = 'status-offline';
    }
}

try {
    videoPlayer.muted = true;
    videoPlayer.setAttribute('playsinline', '');
} catch (e) {
    console.warn('Could not set muted/playsinline', e);
}

initHls();

videoPlayer.play().then(() => {
    statusElement.textContent = '● Streaming';
    statusElement.className = 'status-online';
    unmuteBtn.classList.remove('hidden');
}).catch(err => {
    console.log('Autoplay prevented or failed:', err);
    statusElement.textContent = '● Click to start streaming';
    statusElement.className = 'status-offline';
    showOverlay();
    unmuteBtn.classList.remove('hidden');
});

overlayPlay.addEventListener('click', function () {
    videoPlayer.play().then(() => {
        overlayPlay.classList.remove('visible');
        statusElement.textContent = '● Streaming';
        statusElement.className = 'status-online';
    }).catch(e => {
        console.warn('Play failed after user click', e);
    });
});

unmuteBtn.addEventListener('click', function (ev) {
    if (videoPlayer.paused) {
        videoPlayer.play().catch(e => console.warn('Play failed on unmute click', e));
    }
    try {
        videoPlayer.muted = false;
        videoPlayer.volume = 0.9;
    } catch (e) {
        console.warn('Could not unmute', e);
    }
    unmuteBtn.classList.add('hidden');
});

videoPlayer.addEventListener('error', function(e) {
    console.error('Video error:', e);
    statusElement.textContent = '● Error loading stream';
    statusElement.className = 'status-offline';
});

videoPlayer.addEventListener('waiting', function() {
    statusElement.textContent = '● Buffering...';
    statusElement.className = '';
});

videoPlayer.addEventListener('playing', function() {
    statusElement.textContent = '● Streaming';
    statusElement.className = 'status-online';
    overlayPlay.classList.remove('visible');
});

videoPlayer.addEventListener('stalled', function() {
    statusElement.textContent = '● Stream stalled, buffering...';
});
</script>
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

@app.route("/watch/<group>/<int:idx>")
def watch_channel(group, idx):
    if group not in PLAYLISTS:
        abort(404)
    channels = get_channels(group)
    if idx < 0 or idx >= len(channels):
        abort(404)
    ch = channels[idx]
    return render_template_string(WATCH_HTML, channel=ch, group=group, idx=idx)

@app.route("/play/<group>/<int:idx>")
def play_channel(group, idx):
    if group not in PLAYLISTS:
        abort(404)
    channels = get_channels(group)
    if idx < 0 or idx >= len(channels):
        abort(404)

    ch = channels[idx]
    source_url = ch["url"]
    is_hls = '.m3u8' in source_url.lower() or source_url.endswith('.m3u')

    if is_hls:
        try:
            playlist_content, content_type = proxy_m3u8(source_url)
            headers = {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Content-Type': content_type,
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
            }
            return Response(playlist_content, headers=headers)
        except Exception as e:
            logging.error("Error proxying HLS stream %s: %s", source_url, e)
            abort(502)
    else:
        try:
            content_type, generator = proxy_direct_stream(source_url)
            headers = {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Content-Type': content_type,
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
            }
            return Response(stream_with_context(generator()), headers=headers)
        except Exception as e:
            logging.error("Error proxying direct stream %s: %s", source_url, e)
            abort(502)

@app.route("/proxy-segment/<path:segment_url>")
def proxy_segment_route(segment_url):
    try:
        decoded_url = requests.utils.unquote(segment_url)
        return proxy_segment(decoded_url)
    except Exception as e:
        logging.error("Error proxying segment %s: %s", segment_url, e)
        abort(502)

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

    headers = {
        "Content-Disposition": f'inline; filename="{group}_{idx}.mp3"',
        'Access-Control-Allow-Origin': '*',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
    }

    return Response(stream_with_context(gen()), mimetype="audio/mpeg", headers=headers)

# ============================================================
# Entry
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("IPTV Restream Server - HLS Browser Playback")
    print("=" * 60)
    print(f"Available categories: {len(PLAYLISTS)}")
    print("Main Categories: All, News, Sports, Entertainment, Kids, Movies, Music")
    print("Countries: India, USA, UK, Canada, Australia, Germany, France")
    print("Languages: English, Hindi, Spanish, French, German, Arabic")
    print("=" * 60)
    print("Starting server on http://0.0.0.0:8000")
    print("=" * 60)
    print("HLS STREAMING FEATURES:")
    print("✓ M3U8 playlists rewritten for browser compatibility")
    print("✓ Individual segment proxying (with CORS)")
    print("✓ CORS headers enabled")
    print("✓ HTML5 video player with HLS support + autoplay-friendly setup")
    print("=" * 60)
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)