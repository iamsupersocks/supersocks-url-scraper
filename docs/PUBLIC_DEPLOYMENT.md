# Public production deployment

This repo is public and standalone. It can be deployed with a production-grade URL-reader shape without copying any private code, cookies, tokens, or app integrations.

## 1. Install the full reader

For normal pages only:

```bash
pip install supersocks-url-scraper
```

For the production setup, use the browser-capable install or Docker image:

```bash
pip install 'supersocks-url-scraper[full,browser]'
```

The browser extra is what enables CloakBrowser fallback for hostile media, JS-rendered pages, bot walls, and soft paywalls.

## 2. Use the normalized environment

Copy the public template:

```bash
cp .env.example .env
```

Generate a local bearer token:

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
```

Put it in `.env` as `API_BEARER_TOKEN`. Keep `.env` private.

The important normalized settings are:

```bash
BROWSER_FALLBACK=1
BROWSER_PROFILE_DIR=/browser-profiles/default
BROWSER_POST_LOAD_WAIT_MS=10000
BROWSER_MAX_CONCURRENCY=1
SEO_FALLBACK=1
ARCHIVE_FALLBACK=1
FETCH_STRATEGY_CACHE_PATH=/data/fetch-strategies.json
SUMMARY_PROVIDER=local
```

Meaning:

- try fast HTTP first;
- try SEO-style variants when HTTP is blocked;
- use CloakBrowser only as fallback;
- reuse a persistent browser profile if the operator provides one;
- keep browser concurrency low because rendering is expensive;
- store only route metadata in the strategy cache;
- summarize locally unless the operator explicitly configures their own provider.

## 3. Run with Docker Compose

```bash
docker compose up -d --build
curl http://127.0.0.1:8768/health | jq
```

The included compose file:

- binds only to `127.0.0.1:8768` by default;
- mounts `./data` to `/data`;
- mounts `./browser-profiles` to `/browser-profiles`;
- enables browser/archive/SEO fallback by default;
- exposes `/health` and `/openapi.json`;
- keeps all operator secrets outside git.

## 4. Call the service

Without auth, if `API_BEARER_TOKEN` is empty:

```bash
curl -s http://127.0.0.1:8768/summarize \
  -H 'content-type: application/json' \
  -d '{"url":"https://example.com/article","length":900}' | jq
```

With auth:

```bash
curl -s http://127.0.0.1:8768/summarize \
  -H 'content-type: application/json' \
  -H 'authorization: Bearer YOUR_PUBLIC_SERVICE_TOKEN' \
  -d '{"url":"https://example.com/article","length":900}' | jq
```

To force the same fallback posture per request, even if the server defaults differ:

```bash
curl -s http://127.0.0.1:8768/summarize \
  -H 'content-type: application/json' \
  -H 'authorization: Bearer YOUR_PUBLIC_SERVICE_TOKEN' \
  -d '{
    "url":"https://www.lepoint.fr/...",
    "length":1200,
    "browser_fallback":true,
    "archive_fallback":true,
    "browser_post_load_wait_ms":10000,
    "browser_max_concurrency":1
  }' | jq
```

## 5. Seed or discover strategy routes

Seed known public media routing metadata:

```bash
mkdir -p data
python3 scripts/seed_strategy_cache.py \
  --seed examples/fetch-strategies.media.seed.json \
  --cache data/fetch-strategies.json
```

There are two metadata-only discovery flows:

- `scripts/discover_strategy.py` probes URLs directly with the local Python reader and updates only `data/fetch-strategies.json`.
- `scripts/discover_source.py` calls a running `/summarize` service, records a per-domain source registry in `data/source-discovery.json`, and updates the strategy cache only when the read quality is acceptable.

Use `discover_source.py` for the Celeste-style loop:

```text
new URL -> /summarize -> quality classification -> source-discovery registry -> optional strategy-cache update
```

```bash
python3 scripts/discover_source.py \
  --base-url http://127.0.0.1:8768 \
  --url 'https://example.com/article'
```

With bearer auth:

```bash
SUPERSOCKS_URL_READER_TOKEN="$API_BEARER_TOKEN" \
python3 scripts/discover_source.py \
  --base-url http://127.0.0.1:8768 \
  --url 'https://example.com/article'
```

Discover routes from representative URLs without storing page content:

```bash
python3 scripts/discover_strategy.py \
  --cache data/fetch-strategies.json \
  --browser-fallback 1 \
  --browser-profile-dir ./browser-profiles/default \
  https://www.lesechos.fr/...
```

The source registry stores only domain, timestamps, status, quality, content type, fetch method, short title, summary length, and warnings.

The cache stores route metadata only, for example:

```json
{
  "lesechos.fr": {
    "fetch_method": "cloak-profile",
    "success_count": 1
  }
}
```

Do not store cookies, page content, provider responses, tokens, or raw HTML in the strategy cache.

## 6. Warm or inspect a browser profile

For sites that need a persistent browser session, warm a profile outside git:

```bash
python3 scripts/browser_profile_probe.py \
  --url 'https://example.com/article' \
  --profile-dir ./browser-profiles/default \
  --headless
```

For manual consent/login/challenge solving on an existing display or VNC session:

```bash
DISPLAY=:91 python3 scripts/browser_profile_probe.py \
  --url 'https://example.com/article' \
  --profile-dir ./browser-profiles/default \
  --no-headless \
  --wait-seconds 180
```

The script prints status, final URL, markers such as CAPTCHA/paywall/cookie consent, and article extraction length. It writes screenshot/HTML diagnostics to `/tmp` by default. Never commit the profile, screenshot, HTML dump, cookies, or sessions.

## 7. Optional external summary provider

The public package does not ship vendor-specific LLM wiring. If you operate your own summarizer, configure the generic HTTP adapter:

```bash
SUMMARY_PROVIDER=http
SUMMARY_PROVIDER_URL=http://127.0.0.1:9000/summarize
SUMMARY_PROVIDER_TOKEN=CHANGE_ME_PROVIDER_TOKEN
SUMMARY_PROVIDER_TIMEOUT=30
```

The adapter posts:

```json
{
  "url": "https://example.com/article",
  "title": "Article title",
  "content_type": "article",
  "length": 900,
  "content": "clean extracted text"
}
```

It accepts JSON `{ "summary": "..." }`, `{ "text": "..." }`, `{ "result": "..." }`, or plain text. If the provider fails, the reader falls back to local extractive summary and adds a warning.

## 8. What not to publish

Never commit:

- `.env` with real tokens;
- `browser-profiles/`;
- cookies, sessions, storage state, or browser cache;
- fetched article content or raw HTML caches;
- private prompts, ranking logic, chat integrations, social-account routes, or hosted-service auth;
- provider keys or vendor-specific private wiring.

The public repo should explain the pattern and provide the hooks. The operator supplies their own tokens, profiles, and private integrations locally.
