#!/usr/bin/env python3
import os
import time
import logging
import random
import requests
import subprocess
from flask import Flask, Response, render_template_string, abort, stream_with_context, request

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

    # Countries
    "india": "https://iptv-org.github.io/iptv/countries/in.m3u",
    "usa": "https://iptv-org.github.io/iptv/countries/us.m3u",
    "uk": "https://iptv-org.github.io/iptv/countries/uk.m3u",
    "uae": "https://iptv-org.github.io/iptv/countries/ae.m3u",
    "saudi": "https://iptv-org.github.io/iptv/countries/sa.m3u",
    "pakistan": "https://iptv-org.github.io/iptv/countries/pk.m3u",

    # Categories
    "news": "https://iptv-org.github.io/iptv/categories/news.m3u",
    "sports": "https://iptv-org.github.io/iptv/categories/sports.m3u",
    "movies": "https://iptv-org.github.io/iptv/categories/movies.m3u",
    "music": "https://iptv-org.github.io/iptv/categories/music.m3u",
    "kids": "https://iptv-org.github.io/iptv/categories/kids.m3u",
    "entertainment": "https://iptv-org.github.io/iptv/categories/entertainment.m3u",

    # Languages (extended list)
    "english":   "https://iptv-org.github.io/iptv/languages/eng.m3u",
    "hindi":     "https://iptv-org.github.io/iptv/languages/hin.m3u",
    "tamil":     "https://iptv-org.github.io/iptv/languages/tam.m3u",
    "telugu":    "https://iptv-org.github.io/iptv/languages/tel.m3u",
    "malayalam": "https://iptv-org.github.io/iptv/languages/mal.m3u",
    "kannada":   "https://iptv-org.github.io/iptv/languages/kan.m3u",
    "marathi":   "https://iptv-org.github.io/iptv/languages/mar.m3u",
    "gujarati":  "https://iptv-org.github.io/iptv/languages/guj.m3u",
    "bengali":   "https://iptv-org.github.io/iptv/languages/ben.m3u",
    "punjabi":   "https://iptv-org.github.io/iptv/languages/pan.m3u",

    # International languages
    "arabic":  "https://iptv-org.github.io/iptv/languages/ara.m3u",
    "urdu":    "https://iptv-org.github.io/iptv/languages/urd.m3u",
    "french":  "https://iptv-org.github.io/iptv/languages/fra.m3u",
    "spanish": "https://iptv-org.github.io/iptv/languages/spa.m3u",
    "german":  "https://iptv-org.github.io/iptv/languages/deu.m3u",
    "turkish": "https://iptv-org.github.io/iptv/languages/tur.m3u",
    "russian": "https://iptv-org.github.io/iptv/languages/rus.m3u",
    "chinese": "https://iptv-org.github.io/iptv/languages/zho.m3u",
    "japanese":"https://iptv-org.github.io/iptv/languages/jpn.m3u",
    "korean":  "https://iptv-org.github.io/iptv/languages/kor.m3u",
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

    url = PLAYLISTS.get(name)
    if not url:
        logging.error("Playlist not found: %s", name)
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
# HTML TEMPLATES
# ============================================================
HOME_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IPTV Restream</title>
<style>
body{background:#000;color:#0f0;font-family:Arial;padding:16px}
a{color:#0f0;text-decoration:none;border:1px solid #0f0;padding:10px;margin:8px;border-radius:8px;display:inline-block}
a:hover{background:#0f0;color:#000}
.search-btn{display:inline-block;padding:8px;border:1px solid #0f0;border-radius:8px;margin-left:8px}
</style>
</head>
<body>
<h2>🌐 IPTV</h2>

<a href="/random" style="background:#0f0;color:#000">🎲 Random Channel</a>
<a href="/favourites" style="border-color:yellow;color:yellow">⭐ Favourites</a>

<form action="/search" method="get" style="display:inline-block;margin-left:8px;">
  <input id="home-search" name="q" placeholder="Search..." style="padding:8px;border-radius:6px;background:#111;border:1px solid #0f0;color:#0f0">
  <button class="search-btn" type="submit">🔍</button>
</form>

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
body{background:#000;color:#0f0;font-family:Arial;padding:12px}
.card{display:flex;align-items:center;gap:10px;border:1px solid #0f0;border-radius:8px;padding:8px;margin:8px 0;background:#111}
.card img{width:42px;height:42px;background:#222;border-radius:6px}
a.btn{border:1px solid #0f0;color:#0f0;padding:6px 8px;border-radius:6px;text-decoration:none;margin-right:8px}
a.btn:hover{background:#0f0;color:#000}
button.k{padding:6px 8px;border-radius:6px;border:1px solid #0f0;background:#111;color:#0f0;margin-left:6px}
input#search{width:60%;padding:8px;border-radius:6px;border:1px solid #0f0;background:#111;color:#0f0}
.keypad{margin-top:8px}
.kbtn{padding:8px;width:36px;border-radius:6px;margin:2px;border:1px solid #0f0;background:#111;color:#0f0}
</style>
</head>
<body>
<h3>{{ group|capitalize }} Channels</h3>
<a href="/">← Back</a>
<a class="btn" href="/random/{{ group }}" style="background:#0f0;color:#000">🎲 Random</a>

<div style="margin-top:10px;">
  <input id="search" placeholder="Type or use keypad..." >
  <button class="k" onclick="doSearch()">🔍</button>
  <button class="k" onclick="clearSearch()">✖</button>
</div>

<!-- optional small keypad for HMD-style input (on-screen) -->
<div class="keypad" role="application">
  <button class="kbtn" onclick="updateSearch('1')">1</button>
  <button class="kbtn" onclick="updateSearch('2')">2</button>
  <button class="kbtn" onclick="updateSearch('3')">3</button>
  <button class="kbtn" onclick="updateSearch('4')">4</button>
  <button class="kbtn" onclick="updateSearch('5')">5</button>
  <button class="kbtn" onclick="updateSearch('6')">6</button>
  <button class="kbtn" onclick="updateSearch('7')">7</button>
  <button class="kbtn" onclick="updateSearch('8')">8</button>
  <button class="kbtn" onclick="updateSearch('9')">9</button>
  <button class="kbtn" onclick="updateSearch('0')">0</button>
</div>

<div id="channelList" style="margin-top:12px;">
{% for ch in channels %}
<div class="card" data-url="{{ ch.url }}" data-title="{{ ch.title }}">
  <div style="font-size:20px;width:40px;text-align:center;color:#0f0">{{ loop.index }}.</div>

  <img src="{{ ch.logo or fallback }}" onerror="this.src='{{ fallback }}'">

  <div style="flex:1">
    <strong>{{ ch.title }}</strong>
    <div style="margin-top:6px">
      <a class="btn" href="/watch/{{ group }}/{{ loop.index0 }}" target="_blank">▶️</a>
      <a class="btn" href="/play-audio/{{ group }}/{{ loop.index0 }}" target="_blank">🎧</a>
      <button class="k" onclick='addFav("{{ ch.title|replace('"','&#34;') }}","{{ ch.url }}","{{ ch.logo }}")'>⭐</button>
    </div>
  </div>
</div>
{% endfor %}
</div>

<script>
/* keypad + search integration */
function updateSearch(ch){
  const inp = document.getElementById('search');
  inp.value = inp.value + ch;
  // do not auto-filter — user will press 🔍 (doSearch)
}

function clearSearch(){
  document.getElementById('search').value = '';
}

function doSearch(){
  const q = document.getElementById('search').value.trim();
  if(!q) {
    alert("Type something to search");
    return;
  }
  // go to the flat search results page
  window.location = '/search?q=' + encodeURIComponent(q);
}

/* favourites client-side */
function addFav(title, url, logo){
  let f = JSON.parse(localStorage.getItem('favs') || '[]');
  // prevent duplicates
  if (!f.find(x => x.url === url)) {
    f.push({title:title, url:url, logo:logo});
    localStorage.setItem('favs', JSON.stringify(f));
    alert('Added to favourites');
  } else {
    alert('Already in favourites');
  }
}
</script>
</body>
</html>
"""

SEARCH_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Search results</title>
<style>
body{background:#000;color:#0f0;font-family:Arial;padding:12px}
.card{display:flex;align-items:center;gap:10px;border:1px solid #0f0;border-radius:8px;padding:8px;margin:8px 0;background:#111}
.card img{width:42px;height:42px;background:#222;border-radius:6px}
a.btn{border:1px solid #0f0;color:#0f0;padding:6px 8px;border-radius:6px;text-decoration:none;margin-right:8px}
button.k{padding:6px 8px;border-radius:6px;border:1px solid #0f0;background:#111;color:#0f0;margin-left:6px}
input#q{width:70%;padding:8px;border-radius:6px;border:1px solid #0f0;background:#111;color:#0f0}
</style>
</head>
<body>
<h3>Search results for: "<span id="term">{{ query }}</span>"</h3>
<a href="/">← Back</a>

<div style="margin-top:10px;">
  <input id="q" value="{{ query }}" placeholder="Search..." >
  <button class="k" onclick="goSearch()">🔍</button>
  <button class="k" onclick="clearBox()">✖</button>
</div>

<div id="results" style="margin-top:12px;">
{% if results %}
  {% for r in results %}
    <div class="card">
      <img src="{{ r.logo or fallback }}" onerror="this.src='{{ fallback }}'">
      <div style="flex:1">
        <strong>{{ r.title }}</strong>
        <div style="margin-top:6px">
          <a class="btn" href="/watch/all/{{ r.index }}" target="_blank">▶ Watch</a>
          <a class="btn" href="/play-audio/all/{{ r.index }}" target="_blank">🎧 Audio</a>
          <button class="k" onclick='addFav("{{ r.title|replace('"','&#34;') }}","{{ r.url }}","{{ r.logo }}")'>⭐</button>
        </div>
      </div>
    </div>
  {% endfor %}
{% else %}
  <div style="padding:16px;border:1px solid #0f0;border-radius:8px">No results found.</div>
{% endif %}
</div>

<script>
function goSearch(){
  const q = document.getElementById('q').value.trim();
  if(!q){ alert("Type something"); return; }
  window.location = '/search?q=' + encodeURIComponent(q);
}
function clearBox(){ document.getElementById('q').value = ''; }

// favourites (same as other pages)
function addFav(title, url, logo){
  let f = JSON.parse(localStorage.getItem('favs') || '[]');
  if (!f.find(x => x.url === url)) {
    f.push({title:title, url:url, logo:logo});
    localStorage.setItem('favs', JSON.stringify(f));
    alert('Added to favourites');
  } else {
    alert('Already in favourites');
  }
}

/* allow pressing Enter key to search */
document.getElementById('q').addEventListener('keydown', function(e){
  if(e.key === 'Enter'){ goSearch(); }
});
</script>
</body>
</html>
"""

WATCH_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ channel.title }}</title>
<style>
body{background:#000;color:#0f0;margin:0;font-family:Arial}
video{width:100%;height:auto;max-height:90vh;border:2px solid #0f0;margin-top:10px}
.btn-reload{
    display:inline-block;
    padding:8px 14px;
    border:1px solid #0f0;
    color:#0f0;
    border-radius:6px;
    text-decoration:none;
    margin:10px;
    cursor:pointer;
}
.btn-reload:hover{background:#0f0;color:#000}
</style>
</head>
<body>
<h3 style="text-align:center">{{ channel.title }}</h3>

<<!-- ⭐ Reload + Favourite Buttons -->
<div style="text-align:center;margin-top:5px;">
  <button class="btn-reload" onclick="reloadVideo()">🔄 Reload</button>
  <button class="btn-reload" onclick="addFavWatch()"
          style="border-color:yellow;color:yellow;margin-left:8px;">
    ⭐ Favourite
  </button>
</div>

<video id="vid" controls autoplay playsinline>
  <source src="{{ channel.url }}" type="{{ mime_type }}">
</video>

<script>
const currentChannel = {
  title: "{{ channel.title|replace('"','&#34;') }}",
  url: "{{ channel.url }}",
  logo: "{{ channel.logo or '' }}"
};

function reloadVideo(){
    const v = document.getElementById("vid");
    const src = v.querySelector("source").src;
    const newSrc = src.split("?")[0] + "?t=" + Date.now();
    v.pause();
    v.querySelector("source").src = newSrc;
    v.load();
    v.play();
}

// same favourites schema used elsewhere
function addFav(title, url, logo){
  let f = JSON.parse(localStorage.getItem('favs') || '[]');
  if (!f.find(x => x.url === url)) {
    f.push({title:title, url:url, logo:logo});
    localStorage.setItem('favs', JSON.stringify(f));
    alert('Added to favourites');
  } else {
    alert('Already in favourites');
  }
}

function addFavWatch(){
  addFav(currentChannel.title, currentChannel.url, currentChannel.logo);
}
</script>

</body>
</html>
"""

FAV_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Favourites</title>
<style>
body{background:#000;color:#0f0;font-family:Arial;padding:12px}
.card{display:flex;align-items:center;gap:10px;border:1px solid yellow;border-radius:8px;padding:8px;margin:8px 0;background:#111}
.card img{width:42px;height:42px;background:#222;border-radius:6px}
a.btn{border:1px solid yellow;color:yellow;padding:6px 8px;border-radius:6px;text-decoration:none;margin-right:8px}
a.btn:hover{background:yellow;color:#000}
</style>
</head>
<body>
<h2>⭐ Favourites</h2>
<a href="/">← Back</a>

<div id="favList" style="margin-top:12px;"></div>

<script>
function loadFavs(){
  let f = JSON.parse(localStorage.getItem('favs') || '[]');
  let html = "";
  f.forEach((c,i)=>{
    html += `
    <div class="card">
      <img src="${c.logo||''}" onerror="this.src='${'""" + LOGO_FALLBACK + """'}'">
      
      <!-- delete button on right side -->
      <button onclick="delFav(${i})" 
              style="background:#000;color:red;border:1px solid red;
                     border-radius:6px;padding:4px 10px;font-size:20px;
                     cursor:pointer;">
        ×
      </button>

      <div style="flex:1">
        <strong>${c.title}</strong>
        <div style="margin-top:6px">
          <a class="btn"
             href="/watch-direct?title=${encodeURIComponent(c.title)}&url=${encodeURIComponent(c.url)}&logo=${encodeURIComponent(c.logo)}"
             target="_blank">▶ Watch</a>
          <a class="btn" href="/play-audio/fav/${i}" target="_blank">🎧 Audio</a>
        </div>
      </div>
    </div>`;
  });
  document.getElementById('favList').innerHTML = html;
}

function delFav(index){
  let f = JSON.parse(localStorage.getItem('favs') || '[]');
  f.splice(index, 1);
  localStorage.setItem('favs', JSON.stringify(f));
  loadFavs();
}
loadFavs();
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
    channels = get_channels(group)
    return render_template_string(LIST_HTML, group=group, channels=channels, fallback=LOGO_FALLBACK)

@app.route("/favourites")
def favourites():
    return render_template_string(FAV_HTML)

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    # if no query, show page with empty results
    if not q:
        return render_template_string(SEARCH_HTML, query="", results=[], fallback=LOGO_FALLBACK)

    ql = q.lower()
    # search in the 'all' playlist for a flat list
    all_channels = get_channels("all")
    results = []
    for idx, ch in enumerate(all_channels):
        title = (ch.get("title") or "").lower()
        group = (ch.get("group") or "").lower()
        # match against title or group or url
        if ql in title or ql in group or ql in (ch.get("url") or "").lower():
            results.append({
                "index": idx,
                "title": ch.get("title"),
                "url": ch.get("url"),
                "logo": ch.get("logo"),
            })
    return render_template_string(SEARCH_HTML, query=q, results=results, fallback=LOGO_FALLBACK)

@app.route("/random")
def random_global():
    channels = get_channels("all")
    if not channels:
        abort(404)
    ch = random.choice(channels)
    url = ch["url"]
    mime = "application/vnd.apple.mpegurl" if ".m3u8" in url else "video/mp4"
    return render_template_string(WATCH_HTML, channel=ch, mime_type=mime)

@app.route("/random/<group>")
def random_category(group):
    if group not in PLAYLISTS:
        abort(404)
    channels = get_channels(group)
    if not channels:
        abort(404)
    ch = random.choice(channels)
    url = ch["url"]
    mime = "application/vnd.apple.mpegurl" if ".m3u8" in url else "video/mp4"
    return render_template_string(WATCH_HTML, channel=ch, mime_type=mime)

@app.route("/watch/<group>/<int:idx>")
def watch_channel(group, idx):
    if group not in PLAYLISTS:
        abort(404)
    channels = get_channels(group)
    if idx < 0 or idx >= len(channels):
        abort(404)
    ch = channels[idx]
    url = ch["url"]
    mime = "application/vnd.apple.mpegurl" if ".m3u8" in url else "video/mp4"
    return render_template_string(WATCH_HTML, channel=ch, mime_type=mime)

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

    headers = {"Access-Control-Allow-Origin": "*"}
    return Response(stream_with_context(gen()), mimetype="audio/mpeg", headers=headers)

@app.route("/watch/fav/<int:index>")
def watch_fav(index):
    try:
        channel = favorites[index]  # your saved favorites list
    except IndexError:
        return "Favorite not found", 404

    mime_type = "application/x-mpegURL" if channel['url'].endswith('.m3u8') else "video/mp4"
    return render_template_string(WATCH_HTML, channel=channel, mime_type=mime_type)


@app.route("/play-audio/fav/<int:index>")
def play_audio_fav(index):
    return """
    <script>
    let f = JSON.parse(localStorage.getItem('favs'))[%d];
    window.location = '/play-audio-direct?u=' + encodeURIComponent(f.url);
    </script>
    """ % index

@app.route("/play-audio-direct")
def play_audio_direct():
    u = request.args.get("u")
    if not u:
        abort(404)
    return Response(stream_with_context(proxy_audio_only(u)),
                    mimetype="audio/mpeg")

@app.route("/watch-direct")
def watch_direct():
    title = request.args.get("title", "Channel")
    url = request.args.get("url")
    logo = request.args.get("logo", "")

    if not url:
        return "Invalid URL", 400

    mime = "application/vnd.apple.mpegurl" if ".m3u8" in url else "video/mp4"

    channel = {
        "title": title,
        "url": url,
        "logo": logo
    }

    return render_template_string(WATCH_HTML, channel=channel, mime_type=mime)

# ============================================================
# Entry
# ============================================================
if __name__ == "__main__":
    print("Running IPTV Restream on http://0.0.0.0:8000")
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)