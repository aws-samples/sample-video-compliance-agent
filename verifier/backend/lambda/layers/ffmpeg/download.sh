#!/bin/bash
cd lambda/layers/ffmpeg
mkdir -p bin
cd bin

# Download static FFmpeg build
curl https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz --output ffmpeg-release-amd64-static.tar.xz
tar xf ffmpeg-release-amd64-static.tar.xz
mv ffmpeg-*-amd64-static/ffmpeg .
mv ffmpeg-*-amd64-static/ffprobe .
rm -rf ffmpeg-*-amd64-static*

# Make binaries executable
chmod +x ffmpeg ffprobe

echo "FFmpeg binaries downloaded and prepared for Lambda layer"