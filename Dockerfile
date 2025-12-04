FROM python:3.11-slim

# Install ffmpeg (needed for audio proxy)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy your full project
COPY . .

# Expose the port used by Gunicorn/Flask
EXPOSE 8000

# Start the app using Gunicorn
# IMPORTANT: The format must be module:variable → restream:app
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "restream:app"]