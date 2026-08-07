#Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# Base AWS Lambda Python 3.12 Slim (Amazon Linux 2 x86_64)
FROM public.ecr.aws/lambda/python:3.12-x86_64

WORKDIR /app

# Install dependencies using dnf
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

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip

# Copy and install requirements
COPY requirements.txt /var/task/
RUN pip install --no-cache-dir -r /var/task/requirements.txt

# Verify installations
RUN python -c "import numpy, cv2; print(f'NumPy: {numpy.__version__}, OpenCV: {cv2.__version__}')"

# Copy function code
COPY . .

# Run as a non-root user (least privilege)
RUN /usr/sbin/groupadd --system --gid 10001 appuser \
    && /usr/sbin/useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app /var/task
USER appuser

# Health check for container orchestrator observability
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD pgrep -f "python" || exit 1

ENTRYPOINT ["python"]
CMD ["frame_selector_v2.py"]