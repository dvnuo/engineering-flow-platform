FROM python:3.9-slim

WORKDIR /app

# Install system tools required by internal bash/github tooling
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates gnupg tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create the workspace directory for this container image's default runtime user (root).
# Note: the canonical runtime workspace model is user-home-based (`~/.efp/workspace`);
# in this image, `~` resolves to `/root`.
RUN mkdir -p /app/skills /app/tools /root/.efp/workspace /root/.efp/skills

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run the application
CMD ["python", "main.py"]
