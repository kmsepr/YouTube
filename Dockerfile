FROM python:3.11-slim

# ---------------------------------------------------
# Install system dependencies
# ---------------------------------------------------
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------
# Install Node.js (needed for yt-dlp JS challenge)
# ---------------------------------------------------
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------------------------------------------------
# Install Python dependencies
# ---------------------------------------------------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install yt-dlp with JS support
RUN pip install --no-cache-dir "yt-dlp[js]" gunicorn

# ---------------------------------------------------
# Copy app
# ---------------------------------------------------
COPY . .

ENV PYTHONUNBUFFERED=1

# ---------------------------------------------------
# Run with Gunicorn
# ---------------------------------------------------
CMD ["gunicorn", "-b", "0.0.0.0:8000", "restream:app"]
