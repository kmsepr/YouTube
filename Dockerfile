# Use an official Python runtime as a parent image
FROM python:3.8-slim

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

# Install system dependencies for ffmpeg and building ffmpeg from source
RUN apt-get update && \
    apt-get install -y \
    build-essential \
    yasm \
    pkg-config \
    wget \
    libx264-dev \
    libfdk-aac-dev \
    libvpx-dev \
    libfreetype6-dev \
    libmp3lame-dev \
    libopus-dev && \
    # Install ffmpeg from source with support for AMR-WB, 3GP, and other codecs
    wget https://ffmpeg.org/releases/ffmpeg-4.4.tar.bz2 && \
    tar -xjf ffmpeg-4.4.tar.bz2 && \
    cd ffmpeg-4.4 && \
    ./configure --enable-gpl --enable-nonfree --enable-libfdk-aac --enable-libx264 --enable-libvpx --enable-libmp3lame --enable-libopus --enable-libfreetype --enable-amr-wb && \
    make && \
    make install && \
    # Clean up
    rm -rf /var/lib/apt/lists/* /ffmpeg-4.4.tar.bz2 /ffmpeg-4.4

# Install any needed Python packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port 5000 available to the world outside this container
EXPOSE 5000

# Define environment variable
ENV NAME World

# Run stream.py when the container launches
CMD ["python", "stream.py"]