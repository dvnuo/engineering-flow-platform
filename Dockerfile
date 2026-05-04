FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /app

# Install Ubuntu system dependencies and Python 3.11.
# Keep Python 3.11 for compatibility with the current native runtime and CI.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        software-properties-common \
        ca-certificates \
        curl \
        gnupg \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-venv \
        python3.11-dev \
        build-essential \
        git \
        tesseract-ocr \
    && python3.11 -m venv "$VIRTUAL_ENV" \
    && "$VIRTUAL_ENV/bin/python" -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies into the virtual environment.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential python3.11-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy application code.
COPY . .

# Create the workspace directory for this container image's default runtime user (root).
# Note: the canonical runtime workspace model is user-home-based (`~/.efp/workspace`);
# in this image, `~` resolves to `/root`.
RUN mkdir -p /app/skills /app/tools /root/.efp/workspace /root/.efp/skills

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "main.py"]
