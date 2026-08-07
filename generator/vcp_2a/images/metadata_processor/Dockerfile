#Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
FROM public.ecr.aws/lambda/python:3.12

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user (least privilege)
RUN dnf install -y shadow-utils && dnf clean all \
    && /usr/sbin/groupadd --system --gid 10001 appuser \
    && /usr/sbin/useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

# Health check for container orchestrator observability
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD pgrep -f "python" || exit 1

ENTRYPOINT ["python"]
CMD ["metadata_processor.py"]