FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (required for yt-dlp JS challenge solver)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install yt-dlp with JavaScript challenge solver
RUN pip install --no-cache-dir "yt-dlp[js]"

# Gunicorn for production
RUN pip install --no-cache-dir gunicorn

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["gunicorn", "-b", "0.0.0.0:8000", "restream:app"]
