FROM python:3.12-slim

ARG INSTALL_EXTRAS=full,browser
ARG PREWARM_BROWSER=1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CLOAKBROWSER_SUPPRESS_FONT_WARNING=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libx11-xcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        wget \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[${INSTALL_EXTRAS}]" \
    && if [ "$PREWARM_BROWSER" = "1" ]; then python -c "from cloakbrowser import ensure_binary; ensure_binary()"; fi

EXPOSE 8768
CMD ["supersocks-url-scraper", "--serve", "--host", "0.0.0.0", "--port", "8768"]
