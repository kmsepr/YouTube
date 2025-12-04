#!/usr/bin/env python3
import os
import time
import logging
import random
import requests
import subprocess
from flask import Flask, Response, render_template_string, abort, stream_with_context

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
# PLAYLISTS (QUALITY REMOVED)
# ============================================================
PLAYLISTS = {
    "all": "https://iptv-org.github.io/iptv/index.m3u",

    # Country
    "india": "https://iptv-org.github.io/iptv/countries/in.m3u",
    "usa": "https://iptv-org.github.io/iptv/countries/us.m3u",
    "uk": "https://iptv-org.github.io/iptv/countries/uk.m3u",

    # Categories
    "news": "https://iptv-org.github.io/iptv/categories/news.m3u",
    "sports": "https://iptv-org.github.io/iptv/categories/sports.m3u",
    "movies": "https://iptv-org.github.io/iptv/categories/movies.m3u",

    # Languages
    "english": "https://iptv-org.github.io/iptv/languages/eng.m3u",
    "hindi": "https://iptv-org.github.io/iptv/languages/hin.m3u",
}

CACHE = {}

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
# HTML — HOME + FAVOURITES
# ============================================================
HOME_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IPTV Restream</title>
<style>
body{background:#000;color:#0f0;font-family:Arial;padding:16px}
a{color:#0f0;text-decoration:none;border:1px solid #0f0;padding:10px;margin:8px;
  border-radius:8px;display:inline-block}
a:hover{background:#0f0;color:#000}
</style>
</head>
<body>
<h2>📺 IPTV Restream</h2>

<a href="/random" style="background:#0f0;color:#000">🎲 Random Channel</a>
<a href="/favourites" style="border-color:yellow;color:yellow">⭐ Favourites</a>

<p>Select a category:</p>
{% for key, url in playlists.items() %}
<a href="/list/{{ key }}">{{ key|capitalize }}</a>
{% endfor %}
</body>
</html>"""

# ============================================================
# LIST PAGE WITH ADD FAV BUTTON
# ============================================================
LIST_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ group|capitalize }} Channels</title>
<style>
body{background:#000;color:#0f0;font-family:Arial;padding:16px}
.card{display:flex;align-items:center;gap:10px;border:1px solid #0f0;border-radius:8px;
      padding:8px;margin:8px 0;background:#111}
.card img{width:42px;height:42px;background:#222;border-radius:6px}
a.btn{border:1px solid #0f0;color:#0f0;padding:6px 8px;border-radius:6px;text-decoration:none;margin-right:8px}
a.btn:hover{background:#0f0;color:#000}
button{padding:6px 8px;border-radius:6px;border:1px solid yellow;color:yellow;background:#222}
input.search{width:100%;padding:10px;border-radius:8px;border:1px solid #0f0;background:#111;color:#0f0;margin-bottom:12px}
</style>
</head>
<body>
<h3>{{ group|capitalize }} Channels</h3>
<a href="/">← Back</a>
<a class="btn" href="/random/{{ group }}" style="background:#0f0;color:#000">🎲 Random</a>

<input class="search" id="search" placeholder="Search..." onkeyup="filterChannels()">

<div id="channelList">
{% for ch in channels %}
<div class="card">
  <img src="{{ ch.logo or fallback }}" onerror="this.src='{{ fallback }}'">
  <div style="flex:1">
    <strong>{{ ch.title }}</strong>
    <div>
      <a class="btn" href="/watch/{{ group }}/{{ loop.index0 }}" target="_blank">▶ Watch</a>
      <a class="btn" href="/play-audio/{{ group }}/{{ loop.index0 }}" target="_blank">🎧 Audio</a>
      <button onclick='addFav("{{ ch.title }}","{{ ch.url }}","{{ ch.logo }}")'>⭐</button>
    </div>
  </div>
</div>
{% endfor %}
</div>

<script>
function filterChannels(){
  let s=document.getElementById('search').value.toLowerCase();
  document.querySelectorAll('.card').forEach(c=>{
    c.style.display=c.innerText.toLowerCase().includes(s)?'':'none';
  });
}

function addFav(title,url,logo){
  let f=JSON.parse(localStorage.getItem("favs")||"[]");
  f.push({title:title,url:url,logo:logo});
  localStorage.setItem("favs",JSON.stringify(f));
  alert("Added to favourites");
}
</script>
</body>
</html>
"""

# ============================================================
# FAVOURITES PAGE
# ============================================================
FAV_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Favourites</title>
<style>
body{background:#000;color:#0f0;font-family:Arial;padding:16px}
.card{border:1px solid yellow;padding:10px;margin:8px 0;border-radius:8px;background:#111}
.card img{width:40px;height:40px;margin-right:8px}
a{color:yellow}
button{background:#222;color:red;border:1px solid red;padding:6px;border-radius:6px}
</style>
</head>
<body>
<h2>⭐ Favourites</h2>
<a href="/">← Back</a>

<div id="favs"></div>

<script>
function loadFavs(){
  let f=JSON.parse(localStorage.getItem("favs")||"[]");
  let html="";
  f.forEach((x,i)=>{
    html+=`
      <div class="card">
        <img src="${x.logo||''}">
        <strong>${x.title}</strong><br>
        <a href="${x.url}" target="_blank">▶ Play</a>
        <button onclick="del(${i})">Delete</button>
      </div>
    `;
  });
  document.getElementById("favs").innerHTML=html;
}
function del(i){
  let f=JSON.parse(localStorage.getItem("favs")||"[]");
  f.splice(i,1);
  localStorage.setItem("favs",JSON.stringify(f));
  loadFavs();
}
loadFavs();
</script>

</body>
</html>
"""

# ============================================================
# WATCH HTML SAME AS BEFORE
# ============================================================
WATCH_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ channel.title }}</title>
<style>
body{background:#000;color:#0f0;margin:0}
video{width:100%;height:auto;max-height:90vh;border:2px solid #0f0;margin-top:10px}
</style>
</head>
<body>
<h3 style="text-align:center">{{ channel.title }}</h3>
<video id="vid" controls autoplay playsinline>
  <source src="{{ channel.url }}" type="{{ mime_type }}">
</video>
</body>
</html>
"""

# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def home():
    return render_template_string(HOME_HTML, playlists=PLAYLISTS)

@app.route("/favourites")
def favourites():
    return render_template_string(FAV_HTML)

@app.route("/list/<group>")
def list_group(group):
    if group not in PLAYLISTS:
        abort(404)
    channels = get_channels(group)
    return render_template_string(LIST_HTML, group=group, channels=channels, fallback=LOGO_FALLBACK)

@app.route("/random")
def random_global():
    channels = get_channels("all")
    ch = random.choice(channels)
    url = ch["url"]
    mime = "application/vnd.apple.mpegurl" if ".m3u8" in url else "video/mp4"
    return render_template_string(WATCH_HTML, channel=ch, mime_type=mime)

@app.route("/random/<group>")
def random_category(group):
    channels = get_channels(group)
    ch = random.choice(channels)
    url = ch["url"]
    mime = "application/vnd.apple.mpegurl" if ".m3u8" in url else "video/mp4"
    return render_template_string(WATCH_HTML, channel=ch, mime_type=mime)

@app.route("/watch/<group>/<int:idx>")
def watch_channel(group, idx):
    channels = get_channels(group)
    ch = channels[idx]
    url = ch["url"]
    mime = "application/vnd.apple.mpegurl" if ".m3u8" in url else "video/mp4"
    return render_template_string(WATCH_HTML, channel=ch, mime_type=mime)

@app.route("/play-audio/<group>/<int:idx>")
def play_channel_audio(group, idx):
    ch = get_channels(group)[idx]
    def gen():
        for chunk in proxy_audio_only(ch["url"]):
            yield chunk
    headers = {"Access-Control-Allow-Origin": "*"}
    return Response(stream_with_context(gen()), mimetype="audio/mpeg", headers=headers)

if __name__ == "__main__":
    print("Running IPTV Restream on http://0.0.0.0:8000")
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)