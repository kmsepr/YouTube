import os
import io
import re
import time
import random
import json
import threading
import subprocess
import logging
import pandas as pd
from collections import deque
from logging.handlers import RotatingFileHandler
from flask import Flask, request, send_file, Response, render_template_string, abort, stream_with_context

# ==============================================================
# 🧠 Basic Config
# ==============================================================

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ==============================================================
# 📘 MCQ Converter SECTION
# ==============================================================

def parse_mcqs(text):
    text = text.replace('\r\n', '\n').replace('\r','\n')
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    rows = []
    qno = None
    qtext_lines = []
    opts = {}
    answer = None
    explanation_lines = []
    capturing_expl = False

    for line in lines:
        m_opt = re.match(r'^[\(\[]?([a-dA-D])[\)\:\-]*\s*(.*)', line)
        if m_opt and not capturing_expl:
            opt_text = m_opt.group(2).strip()
            opt_text = re.sub(r'^[\.\)\:\-\s]+', '', opt_text)
            opts[m_opt.group(1).lower()] = opt_text
            continue

        m_ans = re.match(r'^(\d+)\.\s*(?:Answer|Ans)?[:\-]?\s*([A-Da-d])$', line)
        if m_ans:
            answer = m_ans.group(2).upper()
            qno = m_ans.group(1)
            capturing_expl = True
            explanation_lines = []
            continue

        if capturing_expl:
            if re.match(r'^\d+\.', line):
                if opts and answer:
                    question_full = '\n'.join(qtext_lines).strip() + '\n' + \
                        f"A) {opts.get('a','')}\nB) {opts.get('b','')}\nC) {opts.get('c','')}\nD) {opts.get('d','')}"
                    rows.append([
                        qno,
                        question_full,
                        'A','B','C','D',
                        {"A":1,"B":2,"C":3,"D":4}[answer],
                        ' '.join(explanation_lines).strip(),
                        ""
                    ])
                qno = None
                qtext_lines = []
                opts = {}
                answer = None
                capturing_expl = False
                m_q = re.match(r'^(\d+)\.(.*)', line)
                if m_q:
                    qno = m_q.group(1)
                    qtext_lines = [m_q.group(2).strip()]
                continue
            else:
                explanation_lines.append(line)
                continue

        m_q = re.match(r'^(\d+)\.(.*)', line)
        if m_q:
            qno = m_q.group(1)
            qtext_lines = [m_q.group(2).strip()]
            continue

        if qno:
            qtext_lines.append(line)

    if opts and answer:
        question_full = '\n'.join(qtext_lines).strip() + '\n' + \
            f"A) {opts.get('a','')}\nB) {opts.get('b','')}\nC) {opts.get('c','')}\nD) {opts.get('d','')}"
        rows.append([
            qno,
            question_full,
            'A','B','C','D',
            {"A":1,"B":2,"C":3,"D":4}[answer],
            ' '.join(explanation_lines).strip(),
            ""
        ])

    return rows


@app.route("/mcq", methods=["GET"])
def index_mcq():
    return """
    <!DOCTYPE html>
    <html>
    <head>
    <title>MCQ Converter</title>
    <style>
    body {display:flex;justify-content:center;align-items:center;height:100vh;font-family:Arial;background:linear-gradient(135deg,#4facfe,#00f2fe);margin:0;}
    .container{text-align:center;background:white;padding:40px;border-radius:15px;box-shadow:0 10px 25px rgba(0,0,0,0.25);width:80%;max-width:900px;}
    h1{font-size:36px;margin-bottom:20px;color:#222;}
    textarea{width:100%;height:400px;padding:15px;font-size:16px;border-radius:10px;border:1px solid #ccc;resize:vertical;margin-bottom:20px;}
    input[type=submit]{margin-top:20px;background:#007bff;color:white;border:none;padding:15px 30px;font-size:18px;border-radius:10px;cursor:pointer;transition:0.3s;}
    input[type=submit]:hover{background:#0056b3;}
    </style>
    </head>
    <body>
    <div class="container">
        <h1>📘 MCQ to Excel Converter</h1>
        <form method="post" action="/convert">
            <textarea name="mcq_text" placeholder="Paste your MCQs here..."></textarea><br>
            <input type="submit" value="Convert to Excel">
        </form>
    </div>
    </body>
    </html>
    """


@app.route('/convert', methods=['POST'])
def convert():
    text = request.form.get("mcq_text", "").strip()
    if not text:
        return "No text provided!", 400

    rows = parse_mcqs(text)
    if not rows:
        return "Could not parse any MCQs. Please check format.", 400

    df = pd.DataFrame(rows, columns=[
        "Sr. No.","Question Text","Option 1","Option 2","Option 3","Option 4",
        "Correct Option Number (1–4)","Explanation","Image URL"
    ])
    output = io.BytesIO()
    df.to_excel(output, index=False, header=True)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="mcqs.xlsx")


# ==============================================================
# 🎶 YouTube Playlist Radio SECTION
# ==============================================================

LOG_PATH = "/mnt/data/radio.log"
COOKIES_PATH = "/mnt/data/cookies.txt"
CACHE_FILE = "/mnt/data/playlist_cache.json"
os.makedirs(DOWNLOAD_DIR := "/mnt/data/radio_cache", exist_ok=True)
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

handler = RotatingFileHandler(LOG_PATH, maxBytes=5*1024*1024, backupCount=3)
logging.getLogger().addHandler(handler)

PLAYLISTS = {
    "firdous": "https://youtube.com/playlist?list=PLgkREi1Wpr-VMfNuvrgNhZqpjEbT7nxv0",
    "samastha": "https://youtube.com/playlist?list=PLgkREi1Wpr-XgNxocxs3iPj61pqMhi9bv",
    "youtube": "https://youtube.com/playlist?list=PLYKzjRvMAycik6KyPflN03WxNwF2usRIk",
}

PLAY_MODES = {
    "youtube": "normal",
    "firdous": "normal",
    "samastha": "normal",
}

STREAMS_RADIO = {}
MAX_QUEUE = 128
REFRESH_INTERVAL = 10800  # 3 hr


def load_cache_radio():
    if os.path.exists(CACHE_FILE):
        try:
            return json.load(open(CACHE_FILE))
        except:
            return {}
    return {}


def save_cache_radio(data):
    try:
        json.dump(data, open(CACHE_FILE, "w"))
    except Exception as e:
        logging.error(e)


CACHE_RADIO = load_cache_radio()


def get_playlist_ids(url):
    try:
        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-single-json",
            "--no-warnings",
            "--quiet",
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return [entry["id"] for entry in data.get("entries", []) if "id" in entry]
    except Exception as e:
        logging.error(f"get_playlist_ids() failed for {url}: {e}")
        return []


def load_playlist_ids_radio(name, url):
    try:
        ids = get_playlist_ids(url)
        if not ids:
            logging.warning(f"[{name}] ⚠ No videos found — empty list.")
            CACHE_RADIO[name] = []
            save_cache_radio(CACHE_RADIO)
            return []

        mode = PLAY_MODES.get(name, "normal")
        if mode == "shuffle":
            random.shuffle(ids)
        elif mode == "reverse":
            ids.reverse()

        CACHE_RADIO[name] = ids
        save_cache_radio(CACHE_RADIO)

        logging.info(f"[{name}] Cached {len(ids)} videos.")
        return ids

    except Exception as e:
        logging.error(f"[{name}] playlist load FAIL: {e}")
        return CACHE_RADIO.get(name, [])


def stream_worker_radio(name):
    s = STREAMS_RADIO[name]
    while True:
        try:
            ids = s["IDS"]
            if not ids:
                ids = load_playlist_ids_radio(name, PLAYLISTS[name])
                s["IDS"] = ids
            if not ids:
                time.sleep(10)
                continue

            vid = ids[s["INDEX"] % len(ids)]
            s["INDEX"] += 1
            url = f"https://www.youtube.com/watch?v={vid}"
            logging.info(f"[{name}] ▶ {url}")

            cmd = (
                f'yt-dlp -f "bestaudio/best" --cookies "{COOKIES_PATH}" '
                f'-o - --quiet "{url}" | '
                f'ffmpeg -loglevel quiet -i pipe:0 -ac 1 -ar 44100 -b:a 40k -f mp3 pipe:1'
            )

            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)

            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                while len(s["QUEUE"]) >= MAX_QUEUE:
                    time.sleep(0.05)
                s["QUEUE"].append(chunk)

            proc.wait()
            time.sleep(2)

        except Exception as e:
            logging.error(f"[{name}] Worker error: {e}")
            time.sleep(5)


@app.route("/")
def home():
    playlists = list(PLAYLISTS.keys())
    html = """
    <!doctype html><html><head>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>🎧 YouTube Radio</title>
    <style>
    body{background:#000;color:#0f0;font-family:Arial;text-align:center;padding:12px;}
    a{display:block;color:#0f0;text-decoration:none;border:1px solid #0f0;padding:10px;margin:8px;border-radius:8px;font-size:18px}
    a:hover{background:#0f0;color:#000}
    </style></head><body>
    <h2>🎶 YouTube Playlist Radio</h2>
    <a href="/mcq">🧠 MCQ Converter</a>
    {% for p in playlists %}
      <a href="/stream/{{p}}">▶ {{p|capitalize}}</a>
    {% endfor %}
    </body></html>
    """
    return render_template_string(html, playlists=playlists)


@app.route("/listen/<name>")
def listen_radio_download(name):
    if name not in STREAMS_RADIO:
        abort(404)
    s = STREAMS_RADIO[name]

    def gen():
        while True:
            if s["QUEUE"]:
                yield s["QUEUE"].popleft()
            else:
                time.sleep(0.05)

    headers = {"Content-Disposition": f"attachment; filename={name}.mp3"}
    return Response(stream_with_context(gen()), mimetype="audio/mpeg", headers=headers)


@app.route("/stream/<name>")
def stream_audio(name):
    if name not in STREAMS_RADIO:
        abort(404)
    s = STREAMS_RADIO[name]

    def gen():
        while True:
            if s["QUEUE"]:
                yield s["QUEUE"].popleft()
            else:
                time.sleep(0.05)

    return Response(stream_with_context(gen()), mimetype="audio/mpeg")


def cache_refresher():
    while True:
        for name, url in PLAYLISTS.items():
            if time.time() - STREAMS_RADIO[name]["LAST_REFRESH"] > REFRESH_INTERVAL:
                STREAMS_RADIO[name]["IDS"] = load_playlist_ids_radio(name, url)
                STREAMS_RADIO[name]["LAST_REFRESH"] = time.time()
        time.sleep(1800)


# ==============================================================
# 🚀 ALWAYS INITIALIZE WORKERS (fixes Koyeb/Gunicorn issue)
# ==============================================================

for pname, url in PLAYLISTS.items():
    STREAMS_RADIO[pname] = {
        "IDS": load_playlist_ids_radio(pname, url),
        "INDEX": 0,
        "QUEUE": deque(),
        "LAST_REFRESH": time.time(),
    }
    threading.Thread(target=stream_worker_radio, args=(pname,), daemon=True).start()

threading.Thread(target=cache_refresher, daemon=True).start()


# ==============================================================
# 🚀 LOCAL ONLY
# ==============================================================

if __name__ == "__main__":
    logging.info("Running locally on :8000")
    app.run(host="0.0.0.0", port=8000)