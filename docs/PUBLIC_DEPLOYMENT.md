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
SERVICE_BROWSER_ENABLED=0
BROWSER_FALLBACK=1
BROWSER_PROFILE_DIR=/browser-profiles/default
BROWSER_POST_LOAD_WAIT_MS=10000
BROWSER_POST_LOAD_WAIT_MS_MAX=15000
BROWSER_MAX_CONCURRENCY=1
SEO_FALLBACK=1
ARCHIVE_FALLBACK=1
FETCH_STRATEGY_CACHE_PATH=/data/fetch-strategies.json
DOCUMENT_MAX_PAGES=50
SUMMARY_PROVIDER=local
```

Meaning:

- try fast HTTP first;
- try SEO-style variants when HTTP is blocked;
- in service mode, do not launch CloakBrowser unless `SERVICE_BROWSER_ENABLED=1`;
- reuse a persistent browser profile if the operator provides one;
- keep browser concurrency low because rendering is expensive;
- store only route metadata in the strategy cache;
- summarize locally unless the operator explicitly configures their own provider.

Operator settings (`FETCH_STRATEGY_CACHE_PATH`, `BROWSER_PROFILE_DIR`, `BROWSER_MAX_CONCURRENCY`, `SUMMARY_PROVIDER*`, `SERVICE_BROWSER_ENABLED`, and the page/wait caps) come from the environment only. A request cannot set them.

## 3. Run with Docker Compose

```bash
docker compose up -d --build
curl http://127.0.0.1:8768/health | jq
```

The included compose file:

- binds only to `127.0.0.1:8768` by default;
- mounts `./data` to `/data`;
- mounts `./browser-profiles` to `/browser-profiles`;
- leaves archive/SEO fallback on and the service browser off unless `SERVICE_BROWSER_ENABLED=1`;
- exposes `/health` and `/openapi.json`;
- disables core dumps for the scraper service (`ulimits.core` soft/hard = 0);
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
    "url":"https://consent-paywall-publisher.example/articles/profile-backed-story",
    "length":1200,
    "browser_fallback":true,
    "archive_fallback":true,
    "browser_post_load_wait_ms":10000
  }' | jq
```

`browser_fallback` is accepted. With `SERVICE_BROWSER_ENABLED` unset it does not launch a browser; the JSON `warnings` include `browser fallback disabled in service mode`, and archive/HTTP fallbacks still run. Keys outside the allowlist, such as `browser_max_concurrency` or `strategy_cache_path`, are ignored with `ignored request key: <name>`. The value is not echoed.

## 5. Seed or discover strategy routes

The shipped public seed is a structural template keyed only by reserved `.example` domains. It demonstrates route categories and is not a list of tested publishers:

```bash
mkdir -p data
python3 scripts/seed_strategy_cache.py \
  --seed examples/fetch-strategies.media.seed.json \
  --cache data/fetch-strategies.json
```

For real operations, keep your representative list private and local:

```bash
mkdir -p data
$EDITOR data/representative-urls.txt  # one URL per line
python3 scripts/discover_strategy.py \
  --urls-file data/representative-urls.txt \
  --cache data/fetch-strategies.json \
  --browser-fallback 1 \
  --browser-profile-dir ./browser-profiles/default
```

Review the printed metadata-only results and `data/fetch-strategies.json`. If you maintain a private seed, merge it locally with:

```bash
python3 scripts/seed_strategy_cache.py \
  --seed data/private-fetch-strategies.seed.json \
  --cache data/fetch-strategies.json \
  --overwrite
```

`data/` is gitignored. Never commit real URLs, domains, cookies, fetched content, raw HTML, screenshots, browser profiles, tokens, or private seeds.

There are two metadata-only discovery flows:

- `scripts/discover_strategy.py` probes URLs directly with the local Python reader and updates only `data/fetch-strategies.json`.
- `scripts/discover_source.py` calls a running `/summarize` service, records a per-domain source registry in `data/source-discovery.json`, and updates the strategy cache only when the read quality is acceptable.

Use `discover_source.py` for a service-backed discovery loop:

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
  --urls-file data/representative-urls.txt \
  --cache data/fetch-strategies.json \
  --browser-fallback 1 \
  --browser-profile-dir ./browser-profiles/default
```

The source registry stores only domain, timestamps, status, quality, content type, fetch method, short title, summary length, and warnings.

The cache stores route metadata only, for example:

```json
{
  "consent-paywall-publisher.example": {
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

### Cloak-first Reddit / Instagram / Facebook on Docker/VPS

These platforms route through CloakBrowser first (`fetch_method=cloak` or `cloak-profile`). Install the `browser` extra (already in the default Docker image).

```bash
# Optional social-only profile (falls back to BROWSER_PROFILE_DIR)
SOCIAL_BROWSER_PROFILE_DIR=/browser-profiles/social

# Headless by default. Headed warm-up requires an existing display:
# start/attach Xvfb or VNC yourself — this package never installs or starts Xvfb.
CLOAK_HEADLESS=1
# DISPLAY=:99
# CLOAK_HEADLESS=0

# Desktop fallbacks stay off unless explicitly enabled:
SOCIAL_OPENCLI_FALLBACK=0
RDT_CLI_FALLBACK=0
```

Initialize a profile once on a VPS with an existing display, then reuse it headless:

```bash
mkdir -p browser-profiles/social
DISPLAY=:99 python3 scripts/browser_profile_probe.py \
  --url 'https://www.reddit.com/r/announcements/' \
  --profile-dir ./browser-profiles/social \
  --no-headless \
  --wait-seconds 120

BROWSER_PROFILE_DIR=./browser-profiles/social \
supersocks-url-scraper https://www.reddit.com/r/announcements/
```

Public smoke URLs (no account required; sites may still gate guests):

- `https://www.reddit.com/r/announcements/`
- `https://www.instagram.com/instagram/`
- `https://www.facebook.com/facebook`

Limits: login/CAPTCHA/consent walls return `partial`/`error` with actionable warnings. Never automate login/MFA/CAPTCHA. Cookies never leave the operator-provided profile directory. OpenCLI and rdt-cli are opt-in only and never auto-installed.

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

It accepts JSON `{ "summary": "..." }`, `{ "text": "..." }`, `{ "result": "..." }`, or plain text. If the provider fails, the reader falls back to local extractive summary and adds a warning. Never commit real API keys.

## 8. Service security

`--serve` accepts only these JSON fields: `url`, `length`, `include_content`, `seo_fallback`, `browser_fallback`, `archive_fallback`, `jina_fallback`, `browser_post_load_wait_ms`, and `document_max_pages`. `browser_post_load_wait_ms` is clamped to `0..BROWSER_POST_LOAD_WAIT_MS_MAX` (default max 15000). `document_max_pages` is clamped to `1..DOCUMENT_MAX_PAGES`. Any other key is ignored and named in `warnings`. The CLI flags are unchanged.

Direct HTTP(S) reads, SEO variants, archive lookups, document/PDF downloads, the LinkedIn HTML fetch, and YouTube subtitle fetches refuse destinations that are not globally reachable:

- loopback, unspecified, link-local, and multicast;
- private RFC1918 ranges;
- CGNAT `100.64.0.0/10` (the shared range used by carrier-grade NAT and Tailscale);
- cloud metadata `169.254.169.254`;
- IPv6 ULA `fc00::/7` and link-local `fe80::/10`;
- IPv4-mapped (`::ffff:`), IPv4-compatible, NAT64 `64:ff9b::/96`, 6to4, and Teredo forms when the embedded address is blocked.

The name is checked again when the socket connects. If any resolved address is blocked, the host is refused and no connection is opened. Redirects are checked the same way. These reads ignore `HTTP_PROXY` and `HTTPS_PROXY`. The summary-provider URL is exempt because only the operator can set it, through `SUMMARY_PROVIDER_URL`.

Chromium is off in service mode. Playwright does not see every redirect and cannot pin DNS for scripts or subresources, so interception would not meet the private-egress rule. `SERVICE_BROWSER_ENABLED=1` turns the old first-URL check back on and is outside that guarantee. Cached `cloak` / `cloak-profile` strategies and Cloak-first Reddit, Instagram, and Facebook reads are skipped, with `browser fallback disabled in service mode`. JS-heavy or paywalled pages can return `partial`. yt-dlp, twitter-cli, opencli, rdt-cli, and Jina stay on fixed public hosts or an explicit operator opt-in. They are a residual limit, not an open proxy.

## 9. Deploy and roll back

Run this on the host that already runs the container. Do not commit `.env`, profiles, or fetched pages.

1. Archive the deployed directory.
2. Tag the image that is running: `docker tag supersocks-url-scraper:latest supersocks-url-scraper:pre-2219`.
3. Copy this tree into the deployed directory, leaving `.env`, `data/`, and `browser-profiles/` in place.
4. `docker compose build && docker compose up -d`.
5. Check `/health`, one public article, one private literal URL (refused), and a body that includes `strategy_cache_path` (ignored, with a warning, and no file written outside the operator cache path).

Roll back without rebuilding:

1. `docker tag supersocks-url-scraper:pre-2219 supersocks-url-scraper:latest`
2. `docker compose up -d`

Restore the archived directory if the copied source also has to go back.

## 10. What not to publish

Never commit:

- `.env` with real tokens;
- `browser-profiles/`;
- cookies, sessions, storage state, or browser cache;
- fetched article content or raw HTML caches;
- private prompts, ranking logic, chat integrations, social-account routes, or hosted-service auth;
- provider keys or vendor-specific private wiring.

The public repo should explain the pattern and provide the hooks. The operator supplies their own tokens, profiles, and private integrations locally.
