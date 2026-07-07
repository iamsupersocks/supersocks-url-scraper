# Supersocks URL Scraper

A small, dependency-light URL reader/scraper for extracting a page title, metadata, readable summary, publication date, content type, and extraction warnings.

It is designed for agent pipelines, RSS/news tooling, and local automation where you want a simple JSON contract without a browser stack.

## Features

- No required third-party runtime dependencies.
- CLI one-shot mode.
- Optional HTTP service with `/health` and `/summarize`.
- Extracts from:
  - OpenGraph/Twitter/HTML metadata
  - JSON-LD article objects
  - readable `<p>` paragraphs
- Returns warnings for partial extraction.
- Does **not** execute JavaScript.
- Safe to run locally or in cron/server contexts.

## Limitations

This scraper intentionally does not run a browser. It may return partial or boilerplate content for:

- JavaScript-heavy pages
- login walls
- cookie walls
- bot checks / CAPTCHA pages
- social sites that hide content from simple HTTP clients

When that happens, check the `status` and `warnings` fields.

## Install

```bash
pip install supersocks-url-scraper
```

Or from a local checkout:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

## CLI usage

```bash
supersocks-url-scraper https://example.com/article
```

With longer summary:

```bash
supersocks-url-scraper --length 1500 https://example.com/article
```

Include cleaned page content:

```bash
supersocks-url-scraper --include-content https://example.com/article
```

## HTTP service

Start the service:

```bash
supersocks-url-scraper --serve --host 127.0.0.1 --port 8768
```

Health check:

```bash
curl http://127.0.0.1:8768/health
```

Summarize a URL:

```bash
curl -s http://127.0.0.1:8768/summarize \
  -H 'content-type: application/json' \
  -d '{"url":"https://example.com/article","length":900}' | jq
```

Response shape:

```json
{
  "status": "ok",
  "url": "https://example.com/article",
  "title": "Article title",
  "summary": "Readable summary text...",
  "published": "2026-01-01T12:00:00Z",
  "content_type": "text/html; charset=utf-8",
  "warnings": [],
  "reader": "supersocks-url-scraper/0.1"
}
```

## Python usage

```python
from supersocks_url_scraper import read_url

result = read_url("https://example.com/article", length=1200)
print(result["title"])
print(result["summary"])
```

## Docker

```bash
docker build -t supersocks-url-scraper .
docker run --rm -p 8768:8768 supersocks-url-scraper
```

## Privacy / public-safety note

This repository is intentionally standalone and does not include:

- private Telegram code
- tokens or credentials
- private logs
- private user paths
- LLMgram-specific config
- upstream/internal-project-specific config

## License

MIT
