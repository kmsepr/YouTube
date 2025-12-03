# Use official minimal Python image
FROM python:3.10-slim

# Install system packages needed for yt-dlp + ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first (for docker caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Make sure tmp mp3 directory exists
RUN mkdir -p /tmp/ytmp3

# Expose Flask port
EXPOSE 8000

# Run the Flask app
CMD ["python", "restream.py"]
