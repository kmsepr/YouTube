#!/usr/bin/env python3
import time
import threading
import logging
import requests
import subprocess
import os
from flask import Flask, Response, render_template_string, abort, stream_with_context, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
app = Flask(__name__)

# -----------------------
# Your existing TV Streams (direct m3u8)
# -----------------------
TV_STREAMS = {
    "safari_tv": "https://j78dp346yq5r-hls-live.5centscdn.com/safari/live.stream/chunks.m3u8",
    "dd_sports": "https://cdn-6.pishow.tv/live/13/master.m3u8",
    "dd_malayalam": "https://d3eyhgoylams0m.cloudfront.net/v1/manifest/93ce20f0f52760bf38be911ff4c91ed02aa2fd92/ed7bd2c7-8d10-4051-b397-2f6b90f99acb/562ee8f9-9950-48a0-ba1d-effa00cf0478/2.m3u8",
    "mazhavil_manorama": "https://yuppmedtaorire.akamaized.net/v1/master/a0d007312bfd99c47f76b77ae26b1ccdaae76cb1/mazhavilmanorama_nim_https/050522/mazhavilmanorama/playlist.m3u8",
    "victers_tv": "https://932y4x26ljv8-hls-live.5centscdn.com/victers/tv.stream/chunks.m3u8",
    "bloomberg_tv": "https://bloomberg.com/media-manifest/streams/us.m3u8",
    "france_24": "https://live.france24.com/hls/live/2037218/F24_EN_HI_HLS/master_500.m3u8",
    "aqsa_tv": "http://167.172.161.13/hls/feedspare/6udfi7v8a3eof6nlps6e9ovfrs65c7l7.m3u8",
    "mult": "http://stv.mediacdn.ru/live/cdn/mult/playlist.m3u8",
    "yemen_today": "https://video.yementdy.tv/hls/yementoday.m3u8",
    "yemen_shabab": "https://starmenajo.com/hls/yemenshabab/index.m3u8",
    "al_sahat": "https://assahat.b-cdn.net/Assahat/assahatobs/index.m3u8",
}

CHANNEL_LOGOS = {
    "safari_tv": "https://i.imgur.com/dSOfYyh.png",
    "victers_tv": "https://i.imgur.com/kj4OEsb.png",
    "bloomberg_tv": "https://i.imgur.com/OuogLHx.png",
    "france_24": "https://upload.wikimedia.org/wikipedia/commons/c/c1/France_24_logo_%282013%29.svg",
    "aqsa_tv": "https://i.imgur.com/Z2rfrQ8.png",
    "mazhavil_manorama": "https://i.imgur.com/fjgzW20.png",
    "dd_malayalam": "https://i.imgur.com/ywm2dTl.png",
    "dd_sports": "https://i.imgur.com/J2Ky5OO.png",
    "mult": "https://i.imgur.com/xi351Fx.png",
    "yemen_today": "https://i.imgur.com/8TzcJu5.png",
    "yemen_shabab": "https://i.imgur.com/H5Oi2NS.png",
    "al_sahat": "https://i.imgur.com/UVndAta.png",
}

# -----------------------
# IPTV-org playlists (Option 2 chosen) — categories mapped to iptv-org m3u urls
# -----------------------
PLAYLISTS = {
    "all": "https://iptv-org.github.io/iptv/index.m3u",
    "india": "https://iptv-org.github.io/iptv/countries/in.m3u",
    "news": "https://iptv-org.github.io/iptv/categories/news.m3u",
    "sports": "https://iptv-org.github.io/iptv/categories/sports.m3u",
    "entertainment": "https://iptv-org.github.io/iptv/categories/entertainment.m3u",
    "kids": "https://iptv-org.github.io/iptv/categories/kids.m3u",
    "movies": "https://iptv-org.github.io/iptv/categories/movies.m3u",
    "music": "https://iptv-org.github.io/iptv/categories/music.m3u",
    "documentary": "https://iptv-org.github.io/iptv/categories/documentary.m3u",
    "regional": "https://iptv-org.github.io/iptv/categories/regional.m3u",
    "religious": "https://iptv-org.github.io/iptv/categories/religious.m3u",
    "english": "https://iptv-org.github.io/iptv/languages/eng.m3u",
    "hindi": "https://iptv-org.github.io/iptv/languages/hin.m3u",
    "arabic": "https://iptv-org.github.io/iptv/languages/ara.m3u",
}

# Cache for playlist parsing
REFRESH_INTERVAL = 1800  # 30 minutes
CACHE = {}

# ============================================================
# M3U Parsing (same robust parser)
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

def get_channels(name: str):
    now = time.time()
    cached = CACHE.get(name)
    if cached and now - cached.get("time", 0) < REFRESH_INTERVAL:
        return cached["channels"]

    url = PLAYLISTS.get(name)
    if not url:
        return []

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
# Audio transcoding helper (40kbps mono mp3)
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
# TEMPLATES
# - main home includes TV tab and IPTV tab
# - IPTV pages: category listing, channel list, watch player, favourites
# ============================================================
MAIN_HOME = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TV + IPTV</title>
<style>
body{background:#000;color:#0f0;font-family:Arial,Helvetica,sans-serif;margin:0;padding:12px}
.header{display:flex;gap:8px;align-items:center}
.tab{padding:8px 12px;border-radius:8px;background:#111;color:#0f0;text-decoration:none}
.container{margin-top:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px}
.card{background:#111;padding:10px;border-radius:8px;text-align:center}
.card img{width:100%;height:80px;object-fit:contain}
</style>
</head>
<body>
<div class="header">
  <a class="tab" href="/">TV Home</a>
  <a class="tab" href="/iptv">IPTV</a>
</div>

<div class="container">
  <h3>TV Streams</h3>
  <div class="grid">
    {% for key in tv_channels %}
      <div class="card">
        <img src="{{ logos.get(key,'https://iptv-org.github.io/assets/logo.png') }}">
        <div style="margin-top:8px">{{ key.replace('_',' ').title() }}</div>
        <div style="margin-top:8px">
          <a href="/watch/{{ key }}" style="color:#0ff">▶ Watch</a> |
          <a href="/audio/{{ key }}" style="color:#ff0">🎵 Audio</a>
        </div>
      </div>
    {% endfor %}
  </div>

  <hr style="margin:18px 0;border-color:#222;">
  <p style="opacity:.7;font-size:13px">IPTV section loads playlists from iptv-org (selected categories). Use the IPTV tab to browse categories and favourites.</p>
</div>
</body>
</html>
"""

IPTV_HOME = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IPTV Categories</title>
<style>
body{background:#000;color:#0f0;font-family:Arial,Helvetica,sans-serif;margin:0;padding:12px}
.header{display:flex;gap:8px;align-items:center}
.tab{padding:8px 12px;border-radius:8px;background:#111;color:#0f0;text-decoration:none}
.catlist{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.cat{background:#111;padding:8px;border-radius:8px}
</style>
</head>
<body>
<div class="header">
  <a class="tab" href="/">TV Home</a>
  <a class="tab" href="/iptv">IPTV</a>
  <a class="tab" href="/iptv/favourites">⭐ Favourites</a>
</div>

<div style="margin-top:14px">
  <h3>IPTV Categories</h3>
  <div class="catlist">
    {% for k in playlists %}
      <div class="cat"><a href="/iptv/list/{{ k }}" style="color:#0f0;text-decoration:none">{{ k.title() }}</a></div>
    {% endfor %}
  </div>
  <p style="opacity:.7;margin-top:12px">Playlists are fetched from iptv-org. Channels can be added to favourites (stored in your browser).</p>
</div>
</body>
</html>
"""

IPTV_LIST = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ group|capitalize }} Channels</title>
<style>
body{background:#000;color:#0f0;font-family:Arial,Helvetica,sans-serif;margin:0;padding:12px}
.card{display:flex;align-items:center;gap:10px;border:1px solid #0f0;border-radius:8px;padding:8px;margin:8px 0;background:#111}
.card img{width:42px;height:42px;object-fit:contain;background:#222;border-radius:6px}
.btns a{border:1px solid #0f0;padding:6px 8px;border-radius:6px;margin-right:8px;display:inline-block;text-decoration:none;color:#0f0}
.fav{cursor:pointer;font-size:18px;padding:4px 8px;border-radius:6px}
</style>
</head>
<body>
<h3>Category: {{ group|capitalize }}</h3>
<p><a href="/iptv">← Back to Categories</a></p>

{% if channels %}
<div>{{ channels|length }} channels found</div>
{% for ch in channels %}
<div class="card" id="card-{{ loop.index0 }}">
  <img src="{{ ch.logo or fallback }}" alt="logo" onerror="this.src='{{ fallback }}'">
  <div style="flex:1">
    <div><strong>{{ loop.index0 }}.</strong> {{ ch.title }}</div>
    <div style="opacity:.8;font-size:12px">{{ ch.group }}{% if ch.tvg_id %} · {{ ch.tvg_id }}{% endif %}</div>
    <div style="margin-top:6px" class="btns">
      <a href="/iptv/watch/{{ group }}/{{ loop.index0 }}" target="_blank">▶ Watch Video</a>
      <a href="/iptv/audio/{{ group }}/{{ loop.index0 }}" target="_blank">🎧 Audio only</a>
      <button class="fav" onclick="toggleFav('{{ group }}|{{ loop.index0 }}')" id="fav-{{ group }}-{{ loop.index0 }}">☆</button>
    </div>
  </div>
</div>
{% endfor %}
{% else %}
<div style="color:#f88;padding:18px;border:1px solid #f88;border-radius:8px">No channels available for this category.</div>
{% endif %}

<script>
function getFavs(){ return JSON.parse(localStorage.getItem('iptv_favs')||"[]"); }
function setFavs(v){ localStorage.setItem('iptv_favs', JSON.stringify(v)); }
function toggleFav(id){
  let favs = getFavs();
  if(favs.includes(id)) favs = favs.filter(x=>x!=id);
  else favs.push(id);
  setFavs(favs);
  updateStars();
}
function updateStars(){
  let favs = getFavs();
  {% for ch in channels %}
    (function(){ let id="{{ group }}|{{ loop.index0 }}"; let btn=document.getElementById("fav-{{ group }}-{{ loop.index0 }}"); if(btn){ btn.innerText = favs.includes(id)? "⭐":"☆"; } })();
  {% endfor %}
}
updateStars();
</script>

</body>
</html>
"""

IPTV_WATCH = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ channel.title }}</title>
<script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.1/dist/hls.min.js"></script>
<style>
body{background:#000;color:#0f0;font-family:Arial,Helvetica,sans-serif;margin:0;padding:12px}
#player{width:100%;max-width:900px;height:60vh;background:#000;border:2px solid #0f0;display:block;margin:12px auto}
.controls{display:flex;justify-content:center;gap:8px;margin-top:8px}
a{color:#0f0;text-decoration:none}
</style>
</head>
<body>
<h3 style="text-align:center">🎬 {{ channel.title }}</h3>
<video id="player" controls autoplay muted playsinline></video>

<div class="controls">
  <a href="/iptv/list/{{ group }}">← Back</a>
  <a href="/iptv/watch/{{ group }}/{{ prev_idx }}">⏮ Prev</a>
  <a href="/iptv/watch/{{ group }}/{{ next_idx }}">⏭ Next</a>
  <a href="/iptv/watch/{{ group }}/{{ idx }}">🔄 Reload</a>
</div>

<script>
const url = "{{ channel.url }}";
const video = document.getElementById('player');
if(video.canPlayType('application/vnd.apple.mpegurl')){
  video.src = url;
} else if(window.Hls && Hls.isSupported()){
  const hls = new Hls();
  hls.loadSource(url);
  hls.attachMedia(video);
} else {
  alert("HLS not supported");
}

document.addEventListener('keydown', (e)=>{
  if(e.key==='4') location.href="/iptv/watch/{{ group }}/{{ prev_idx }}";
  if(e.key==='6') location.href="/iptv/watch/{{ group }}/{{ next_idx }}";
  if(e.key==='0') location.href="/iptv";
  if(e.key==='5') video.paused?video.play():video.pause();
  if(e.key==='9') location.reload();
});
</script>
</body>
</html>
"""

IPTV_FAVS = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IPTV Favourites</title>
<style>
body{background:#000;color:#0f0;font-family:Arial,Helvetica,sans-serif;margin:0;padding:12px}
.card{border:1px solid #0f0;padding:8px;margin:8px 0;border-radius:8px}
a{color:#0f0;text-decoration:none}
</style>
</head>
<body>
<h3>⭐ IPTV Favourites</h3>
<p><a href="/iptv">← Back to categories</a></p>
<div id="list"></div>

<script>
function getFavs(){ return JSON.parse(localStorage.getItem('iptv_favs')||"[]"); }
function render(){
  const favs = getFavs();
  const out = document.getElementById('list');
  if(!favs.length){ out.innerHTML = "<div style='opacity:.7'>No favourites yet.</div>"; return; }
  out.innerHTML = "";
  favs.forEach(id=>{
    // id format: group|index
    const [g,i] = id.split("|");
    const div = document.createElement('div');
    div.className = 'card';
    div.innerHTML = `<div><strong>${g} — ${i}</strong></div>
      <div style="margin-top:8px">
        <a href="/iptv/watch/${g}/${i}" target="_blank">▶ Watch</a> |
        <a href="/iptv/audio/${g}/${i}" target="_blank">🎧 Audio</a> |
        <a href="#" onclick="removeFav('${id}');return false;">Remove</a>
      </div>`;
    out.appendChild(div);
  });
}
function removeFav(id){
  let favs = getFavs();
  favs = favs.filter(x=>x!==id);
  localStorage.setItem('iptv_favs', JSON.stringify(favs));
  render();
}
render();
</script>
</body>
</html>
"""

# ============================================================
# Routes: main home (TV) + IPTV
# ============================================================
@app.route("/")
def home():
    tv_channels = list(TV_STREAMS.keys())
    return render_template_string(MAIN_HOME, tv_channels=tv_channels, logos=CHANNEL_LOGOS)

# TV watch (your existing)
@app.route("/watch/<channel>")
def watch_channel(channel):
    tv_channels = list(TV_STREAMS.keys())
    if channel not in tv_channels:
        abort(404)
    video_url = TV_STREAMS.get(channel)
    current_index = tv_channels.index(channel)
    prev_channel = tv_channels[(current_index - 1) % len(tv_channels)]
    next_channel = tv_channels[(current_index + 1) % len(tv_channels)]

    html = f"""
    <!doctype html>
    <html><head>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{channel.replace('_',' ').title()}</title>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <style>body{{background:#000;color:#fff;text-align:center}}video{{width:95%;max-width:720px}}</style>
    </head><body>
    <h2>{channel.replace('_',' ').title()}</h2>
    <video id="player" controls autoplay playsinline muted></video>
    <div style="margin-top:12px">
      <a href='/'>⬅ Home</a> |
      <a href='/watch/{prev_channel}'>Prev</a> |
      <a href='/watch/{next_channel}'>Next</a> |
      <a href='/watch/{channel}'>Reload</a>
    </div>
    <script>
    const url = "{video_url}";
    const vid = document.getElementById('player');
    if(vid.canPlayType('application/vnd.apple.mpegurl')) vid.src = url;
    else if(window.Hls && Hls.isSupported()){ const h=new Hls(); h.loadSource(url); h.attachMedia(vid); }
    </script>
    </body></html>
    """
    return html

# TV audio (existing)
@app.route("/audio/<channel>")
def audio_only(channel):
    url = TV_STREAMS.get(channel)
    if not url:
        return "Channel not found", 404

    def generate():
        cmd = [
            "ffmpeg", "-i", url,
            "-vn",
            "-ac", "1",
            "-b:a", "40k",
            "-f", "mp3",
            "pipe:1"
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while True:
                data = proc.stdout.read(64 * 1024)
                if not data:
                    break
                yield data
        finally:
            try:
                proc.terminate()
            except Exception:
                pass

    return Response(stream_with_context(generate()), mimetype="audio/mpeg", headers={"Content-Disposition": f'inline; filename="{channel}.mp3"'})

# -----------------------
# IPTV routes
# -----------------------
@app.route("/iptv")
def iptv_home():
    return render_template_string(IPTV_HOME, playlists=list(PLAYLISTS.keys()))

@app.route("/iptv/list/<group>")
def iptv_list(group):
    if group not in PLAYLISTS:
        abort(404)
    channels = get_channels(group)
    return render_template_string(IPTV_LIST, group=group, channels=channels, fallback="https://iptv-org.github.io/assets/logo.png")

@app.route("/iptv/watch/<group>/<int:idx>")
def iptv_watch(group, idx):
    if group not in PLAYLISTS:
        abort(404)
    channels = get_channels(group)
    if idx < 0 or idx >= len(channels):
        abort(404)
    ch = channels[idx]
    prev_idx = (idx - 1) % len(channels)
    next_idx = (idx + 1) % len(channels)
    return render_template_string(IPTV_WATCH, channel=ch, group=group, idx=idx, prev_idx=prev_idx, next_idx=next_idx)

@app.route("/iptv/audio/<group>/<int:idx>")
def iptv_audio(group, idx):
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

@app.route("/iptv/favourites")
def iptv_favourites():
    return render_template_string(IPTV_FAVS)

# ============================================================
# Start
# ============================================================
if __name__ == "__main__":
    print("Starting merged TV + IPTV app on http://0.0.0.0:8000")
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)