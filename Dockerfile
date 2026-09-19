FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/root \
    VIRTUAL_ENV=/opt/venv

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# kubectl release line: pin KUBECTL_VERSION (e.g. v1.32.13) for an exact build,
# or leave it empty to take the latest patch of KUBECTL_STABLE_CHANNEL.
ARG KUBECTL_STABLE_CHANNEL=stable-1.32
ARG KUBECTL_VERSION=""

WORKDIR /app

# Install Ubuntu system dependencies and Python 3.11.
# Keep Python 3.11 for compatibility with the current native runtime and CI.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        software-properties-common \
        ca-certificates \
        curl \
        gnupg \
        unzip \
    && mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --batch --yes --dearmor -o /etc/apt/keyrings/google-linux-signing-key.gpg \
    && chmod a+r /etc/apt/keyrings/google-linux-signing-key.gpg \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-linux-signing-key.gpg] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-venv \
        python3.11-dev \
        build-essential \
        git \
        gh \
        jq \
        tesseract-ocr \
        google-chrome-stable \
    && AWS_CLI_ARCH="$(dpkg --print-architecture)" \
    && case "$AWS_CLI_ARCH" in amd64) AWS_CLI_ARCH="x86_64" ;; arm64) AWS_CLI_ARCH="aarch64" ;; *) echo "Unsupported AWS CLI architecture: $AWS_CLI_ARCH" >&2; exit 1 ;; esac \
    && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_CLI_ARCH}.zip" -o /tmp/awscliv2.zip \
    && unzip -q /tmp/awscliv2.zip -d /tmp \
    && /tmp/aws/install --bin-dir /usr/local/bin --install-dir /usr/local/aws-cli \
    && rm -rf /tmp/aws /tmp/awscliv2.zip \
    # kubectl for read-only EKS inspection. kubectl must stay within one minor
    # version of the target EKS control planes; KUBECTL_STABLE_CHANNEL picks the
    # latest patch of that minor and KUBECTL_VERSION pins an exact release.
    && KUBECTL_ARCH="$(dpkg --print-architecture)" \
    && KUBECTL_VERSION="${KUBECTL_VERSION:-$(curl -fsSL "https://dl.k8s.io/release/${KUBECTL_STABLE_CHANNEL}.txt")}" \
    && curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${KUBECTL_ARCH}/kubectl" \
    && curl -fsSLo /tmp/kubectl.sha256 "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${KUBECTL_ARCH}/kubectl.sha256" \
    && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum --check \
    && chmod 0755 /usr/local/bin/kubectl \
    && rm -f /tmp/kubectl.sha256 \
    && python3.11 -m venv "$VIRTUAL_ENV" \
    && "$VIRTUAL_ENV/bin/python" -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:/usr/local/bin:$PATH" \
    BROWSERSTACK_LOCAL_BINARY="/usr/local/bin/BrowserStackLocal" \
    # Cap glibc malloc arenas: without this a multi-threaded Python process
    # can hold 8*ncpu arenas and park freed turn-memory as resident high-water.
    MALLOC_ARENA_MAX=2

# Install Python dependencies into the virtual environment.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential python3.11-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy application code.
COPY . .

# CI/release must place prebuilt engineering-flow-platform-tools binaries here.
# The runtime image intentionally does not install the Go toolchain. Third-party
# binaries staged next to them (BrowserStackLocal, and the AWS login provider
# adfs-assume or saml2aws that aws-auth login shells out to) are installed the
# same way; scripts/prepare-runtime-tools.sh stages them from *_SOURCE paths.
COPY runtime-tools/ /tmp/runtime-tools/
RUN set -eux; \
    while IFS= read -r -d '' tool; do \
        install -m 0755 "$tool" "/usr/local/bin/$(basename "$tool")"; \
    done < <(find /tmp/runtime-tools -maxdepth 1 -type f ! -name README.md -print0); \
    rm -rf /tmp/runtime-tools; \
    printf '%s\n' '#!/usr/bin/env bash' 'exec /usr/bin/google-chrome-stable --no-sandbox "$@"' > /usr/local/bin/google-chrome; \
    chmod 0755 /usr/local/bin/google-chrome \
    && google-chrome --version >/dev/null \
    && aws --version >/dev/null \
    && kubectl version --client >/dev/null \
    && jq --version >/dev/null \
    && aws-auth version --json >/dev/null \
    && aws-auth commands --json >/dev/null \
    && aws-auth schema login --json >/dev/null \
    && jira version --json >/dev/null \
    && jira commands --json >/dev/null \
    && jira schema issue.map-csv --json >/dev/null \
    && confluence version --json >/dev/null \
    && confluence commands --json >/dev/null \
    && confluence schema page.create --json >/dev/null \
    && jenkins version --json >/dev/null \
    && jenkins commands --json >/dev/null \
    && jenkins schema build.test-report --json >/dev/null \
    && browser version --json >/dev/null \
    && browser commands --json >/dev/null \
    && browser schema probe --json >/dev/null \
    && mobile-auto version --json >/dev/null \
    && mobile-auto commands --json >/dev/null \
    && mobile-auto schema run.start --json >/dev/null

# Create the runtime workspace and external skills directories.
RUN mkdir -p /app/skills /workspace

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "main.py"]
