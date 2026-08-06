# API discovery report (offline HAR)

- Source HAR: `tests/fixtures/api_recipes/discovery_sample.har`
- Generated: `2026-08-06T15:57:18+00:00`
- Entries scanned: **10**
- Candidates kept: **2**
- Excluded: **8**

## Candidates (public HTTPS GET JSON)

| # | Host | Method | Status | Size | Content-Type | JSON keys |
|---|------|--------|--------|------|--------------|-----------|
| 1 | `api.example.com` | GET | 200 | 214 | application/json | `events, total` |
| 2 | `api.example.org` | GET | 200 | 42 | application/json | `items` |

### Candidate URLs (query params redacted)

1. `https://api.example.com/api/v1/events?page=2&limit=50`
2. `https://api.example.org/api/list?cursor=abc123`

## Excluded exchanges

- **1×** excluded: non-GET method
- **1×** excluded: not https
- **1×** excluded: private/loopback/local host
- **1×** excluded: sensitive query param present
- **1×** excluded: sensitive header present
- **1×** excluded: response is not JSON
- **1×** excluded: response body too large
- **1×** excluded: HTTP >= 400

## Candidate recipe (disabled / review_required)

- id: `har-api-api-v1-events` · status: **review_required** · network.mode: `fixture_only`
- This recipe is **disabled** and will never execute or be promoted automatically. It must be reviewed and explicitly activated by an operator.
