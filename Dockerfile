# -------- Base Image --------
FROM python:3.10-slim

# -------- Set working directory --------
WORKDIR /app

# -------- Install system dependencies --------
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# -------- Copy your Python code --------
COPY restream.py /app/restream.py

# -------- Copy cookies file (if available locally) --------
# If cookies.txt is outside the repo, comment this line and mount in Koyeb instead
COPY cookies.txt /app/cookies.txt

# -------- Install Python requirements --------
# (modify if your script needs more)
RUN pip install \
    requests \
    yt-dlp \
    websockets \
    aiohttp

# -------- Environment variables --------
ENV COOKIES_FILE=/app/cookies.txt

# -------- Run the app --------
CMD ["python", "restream.py"]
