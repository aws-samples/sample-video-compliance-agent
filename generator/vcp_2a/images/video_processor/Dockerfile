#Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
FROM public.ecr.aws/lambda/python:3.12

RUN dnf update -y && \
    dnf install -y \
    gcc \
    gcc-c++ \
    python3-devel \
    cmake \
    make \
    libjpeg-turbo \
    libjpeg-turbo-devel \
    libpng \
    libpng-devel \
    libtiff \
    libtiff-devel \
    libwebp \
    libwebp-devel \
    freetype \
    freetype-devel \
    zlib-devel \
    wget \
    tar \
    gzip \
    xz \
    shadow-utils \
    && dnf clean all

# Install FFmpeg from static build
RUN mkdir -p /opt/ffmpeg && \
    cd /opt/ffmpeg && \
    wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz && \
    tar xf ffmpeg-release-amd64-static.tar.xz && \
    rm ffmpeg-release-amd64-static.tar.xz && \
    mv ffmpeg-*-static/* . && \
    rm -rf ffmpeg-*-static && \
    ln -sf /opt/ffmpeg/ffmpeg /usr/local/bin/ && \
    ln -sf /opt/ffmpeg/ffprobe /usr/local/bin/ && \
    # Also create links in /opt/bin to match the paths in the code
    mkdir -p /opt/bin && \
    ln -sf /opt/ffmpeg/ffmpeg /opt/bin/ffmpeg && \
    ln -sf /opt/ffmpeg/ffprobe /opt/bin/ffprobe

# Install OpenCV 
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir numpy==1.26.4 && \
    pip install --no-cache-dir opencv-python-headless==4.8.1.78

# Copy application dependencies
COPY requirements.txt /var/task/
RUN pip install --no-cache-dir -r /var/task/requirements.txt

# Copy function code
COPY video_processor.py /var/task/

WORKDIR /var/task

# Run as a non-root user (least privilege)
RUN /usr/sbin/groupadd --system --gid 10001 appuser \
    && /usr/sbin/useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /var/task
USER appuser

# Health check for container orchestrator observability
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD pgrep -f "python" || exit 1

# Set the entrypoint to our wrapper script
ENTRYPOINT ["python"]
CMD ["video_processor.py"]