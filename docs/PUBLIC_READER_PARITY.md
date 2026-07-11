# Public reader parity for the URL scraper

`supersocks-url-scraper` is a public, standalone URL-reading core for agent pipelines, RSS/news tooling, and local automation. The goal is to keep the package useful and safe without shipping private integrations, credentials, browser profiles, or application-specific automation.

## What should stay compatible

The public service should keep the same operational contract that downstream tools can depend on:

- `GET /health`
- `POST /summarize`
- `POST /read`
- `POST /markdown`
- JSON response fields: `status`, `url`, `content_type`, `title`, `summary`, `length`, `fetch_method`, `warnings`, optional `content`
- fetch methods: `http`, `seo`, `cloak`, `cloak-profile`, `archive`, `fallback`
- layered route: HTTP → SEO variants → CloakBrowser → public archive/cache snapshots
- metadata-only strategy cache by domain, never cookies/content/secrets

## Production-style public defaults

For production deployments, run the public service with explicit environment defaults:

```bash
API_BEARER_TOKEN='change-me' \
BROWSER_FALLBACK=cloak \
BROWSER_PROFILE_DIR=/browser-profiles/default \
BROWSER_POST_LOAD_WAIT_MS=10000 \
BROWSER_MAX_CONCURRENCY=1 \
ARCHIVE_FALLBACK=latest \
FETCH_STRATEGY_CACHE_PATH=/data/fetch-strategies.json \
supersocks-url-scraper --serve --host 127.0.0.1 --port 8768
```

The browser profile path should be mounted/persisted by the operator. The repository must not include profiles, cookies, or tokens.

## Public roadmap

Implemented here:

- optional bearer auth through `API_BEARER_TOKEN`
- service-level defaults for browser fallback, profile dir, post-load wait, archive fallback, summary length, SEO fallback, and strategy cache path
- normalized public deployment template in `.env.example`
- Docker Compose localhost deployment with `/data` and `/browser-profiles` volumes
- public runbook in `docs/PUBLIC_DEPLOYMENT.md` explaining how another operator can reproduce a production-grade setup without private assets

- dependency-free `GET /openapi.json` schema for `/health`, `/summarize`, `/read`, and `/markdown`
- Docker Compose deployment with localhost bind, `/data`, `/browser-profiles`, restart policy, and healthcheck
- source-discovery helper that probes representative URLs and writes only routing metadata
- per-request overrides for the same fields
- CloakBrowser support via the `browser` extra
- Docker image with browser runtime and prewarmed Cloak binary
- public regression corpus in `tests/fixtures/public_regression_corpus.json`, with schema/safety tests and no saved page content
- optional generic HTTP summary provider interface, disabled by default, with no bundled keys or vendor SDK dependency
- source-discovery registry and `scripts/discover_source.py` loop, ported from the internal method but storing only sanitized domain metadata
- browser-profile probe script for operator-owned Cloak profile warm-up/diagnostics, with outputs outside git by default

Still worth adding where it can remain public and generic:

1. Expand the corpus into optional live regression modes that can be run manually against stable URLs without making CI flaky.
2. Add richer provider examples for local/self-hosted summarizers while keeping real credentials out of the repo.

## Privacy and safety boundary

Do not move the following private pieces into this public repo:

- real bearer tokens or API keys
- browser profiles, cookies, sessions, or local user data
- private social-native routes such as account-specific X/Twitter readers
- application-specific prompts, ranking logic, or downstream automation
- saved fetched page content or publisher-specific bypass instructions beyond generic routing metadata
