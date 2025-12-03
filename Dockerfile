# Use Python 3.11 slim base
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Unbuffered output for logs
ENV PYTHONUNBUFFERED=1

# Run the Flask app using Gunicorn
CMD ["gunicorn", "-b", "0.0.0.0:8000", "restream:app"]