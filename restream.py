import os, requests, subprocess, threading, time
from flask import Flask, Response, render_template_string, abort
from urllib.parse import quote

app = Flask(__name__)

# -----------------------
# IPTV Category Playlists
# -----------------------
CATEGORIES = {
    "News": "https://iptv-org.github.io/iptv/categories/news.m3u",
    "Sports": "https://iptv-org.github.io/iptv/categories/sports.m3u",
    "Entertainment": "https://iptv-org.github.io/iptv/categories/entertainment.m3u",
    "Religious": "https://iptv-org.github.io/iptv/categories/religious.m3u",
    "Kids": "https://iptv-org.github.io/iptv/categories/kids.m3u",
    "India": "https://iptv-org.github.io/iptv/countries/in.m3u",
    "Arabic": "https://iptv-org.github.io/iptv/languages/ara.m3u"
}

CACHE = {}  # channel_name -> {url, logo, category}

# -----------------------
# Parse M3U playlist
# -----------------------
def parse_m3u(content, category):
    channels = {}
    lines = content.splitlines()
    for i in range(len(lines)):
        if lines[i].startswith("#EXTINF:"):
            name = lines[i].split(",", 1)[1].strip()
            url = lines[i+1].strip() if i+1 < len(lines) else ""
            logo = ""
            if "tvg-logo=" in lines[i]:
                logo = lines[i].split("tvg-logo=")[1].split()[0].strip('"')
            channels[name] = {"url": url, "logo": logo, "category": category}
    return channels

# -----------------------
# Refresh IPTV cache
# -----------------------
def refresh_cache():
    while True:
        for cat, url in CATEGORIES.items():
            try:
                r = requests.get(url, timeout=10)
                r.raise_for_status()
                channels = parse_m3u(r.text, cat)
                CACHE.update(channels)
            except Exception as e:
                print(f"Error fetching {cat}: {e}")
        time.sleep(3600)  # refresh every hour

threading.Thread(target=refresh_cache, daemon=True).start()

# -----------------------
# Home Page
# -----------------------
@app.route("/")
def home():
    cats = {cat: [c for c in CACHE if CACHE[c]["category"]==cat] for cat in CATEGORIES}
    html = """
<html>
<head>
<title>📺 IPTV</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
body { font-family:sans-serif; background:#111; color:#fff; margin:0; padding:0; }
h2 { text-align:center; margin:10px 0; }
.tabs { display:flex; justify-content:center; background:#000; padding:10px; flex-wrap:wrap; }
.tab { padding:10px 20px; cursor:pointer; background:#222; color:#0ff; border-radius:10px; margin:2px; transition:0.2s; }
.tab.active { background:#0ff; color:#000; }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(120px,1fr)); gap:12px; padding:10px; }
.card { background:#222; border-radius:10px; padding:10px; text-align:center; transition:0.2s; }
.card:hover { background:#333; }
.card img { width:100%; height:80px; object-fit:contain; margin-bottom:8px; }
.card span { font-size:14px; color:#0f0; }
.hidden { display:none; }
</style>
<script>
function showTab(tab){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.grid').forEach(g=>g.classList.add('hidden'));
  document.getElementById(tab).classList.remove('hidden');
  document.getElementById('tab_'+tab).classList.add('active');
}
function toggleFavourite(name){
  let fav = JSON.parse(localStorage.getItem('fav')||"[]");
  if(fav.includes(name)) fav = fav.filter(x=>x!==name);
  else fav.push(name);
  localStorage.setItem('fav', JSON.stringify(fav));
  renderFavourites();
}
function renderFavourites(){
  let fav = JSON.parse(localStorage.getItem('fav')||"[]");
  document.getElementById('fav_grid').innerHTML = '';
  fav.forEach(name=>{
    const ch = window.CHANNELS[name];
    if(!ch) return;
    const div = document.createElement('div');
    div.className='card';
    div.innerHTML=`<img src="${ch.logo||'https://i.imgur.com/0c3q2BL.png'}"><span>${name}</span><br>
    <a href="/watch/${encodeURIComponent(name)}" style="color:#0ff;">▶ Watch</a> |
    <a href="/audio/${encodeURIComponent(name)}" style="color:#ff0;">🎵 Audio</a><br>
    <button onclick="toggleFavourite('${name}')">♥</button>`;
    document.getElementById('fav_grid').appendChild(div);
  });
}
window.onload=()=>{
  showTab('News');
  renderFavourites();
}
</script>
</head>
<body>
<div class="tabs">
  {% for cat in cats %}
  <div class="tab" id="tab_{{cat}}" onclick="showTab('{{cat}}')">{{cat}}</div>
  {% endfor %}
  <div class="tab" id="tab_Favourites" onclick="showTab('Favourites')">♥ Favourites</div>
</div>
{% for cat, ch_list in cats.items() %}
<div id="{{cat}}" class="grid hidden">
{% for key in ch_list %}
<div class="card">
    <img src="{{ CACHE[key]['logo'] or 'https://i.imgur.com/0c3q2BL.png' }}">
    <span>{{ key }}</span><br>
    <a href="/watch/{{ key | urlencode }}" style="color:#0ff;">▶ Watch</a> |
    <a href="/audio/{{ key | urlencode }}" style="color:#ff0;">🎵 Audio</a><br>
    <button onclick="toggleFavourite('{{ key }}')">♥</button>
</div>
{% endfor %}
</div>
{% endfor %}

<div id="Favourites" class="grid hidden" id="fav_grid"></div>

<script>
window.CHANNELS = {{ CACHE | tojson }};
</script>

</body>
</html>
"""
    return render_template_string(html, cats=cats, CACHE=CACHE)

# -----------------------
# Watch Video
# -----------------------
@app.route("/watch/<channel>")
def watch(channel):
    ch = CACHE.get(channel)
    if not ch:
        abort(404)
    url = ch["url"]
    html = f"""
<html>
<head>
<title>{channel}</title>
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
body {{ background:#000; color:#fff; text-align:center; margin:0; padding:10px; }}
video {{ width:95%; max-width:720px; height:auto; background:#000; border:1px solid #333; }}
a {{ color:#0f0; text-decoration:none; margin:10px; display:inline-block; font-size:18px; }}
</style>
</head>
<body>
<h2>{channel}</h2>
<video id="player" controls autoplay playsinline></video>
<div style="margin-top:15px;">
  <a href="/">⬅ Home</a>
</div>
<script>
const video=document.getElementById('player');
const src="{url}";
if(Hls.isSupported()){const hls=new Hls();hls.loadSource(src);hls.attachMedia(video);}
else{video.src=src;}
</script>
</body>
</html>
"""
    return html

# -----------------------
# Audio Only
# -----------------------
@app.route("/audio/<channel>")
def audio_only(channel):
    ch = CACHE.get(channel)
    if not ch:
        abort(404)
    url = ch["url"]
    filename = f"{channel}.mp3"

    def generate():
        cmd = ["ffmpeg","-i",url,"-vn","-ac","1","-b:a","40k","-f","mp3","pipe:1"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while True:
                data = proc.stdout.read(1024)
                if not data: break
                yield data
        finally:
            proc.terminate()

    return Response(generate(), mimetype="audio/mpeg",
                    headers={"Content-Disposition":f'attachment; filename="{filename}"'})

if __name__=="__main__":
    os.makedirs("tmp", exist_ok=True)
    app.run(host="0.0.0.0", port=8000, debug=False)