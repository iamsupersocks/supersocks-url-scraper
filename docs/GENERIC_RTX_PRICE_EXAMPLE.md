# Generic RTX price example

This public example shows how to use the existing `supersocks-url-scraper` runtime to collect structured prices from a synthetic, operator-owned listing page pattern. It is intentionally generic: no real marketplace name, domain, URL, HTML dump, listing identifier, cookie, token, or live result is included.

## Install

From a checkout:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[full,browser,test]'
```

The `browser` extra installs CloakBrowser support for dynamic pages. If your target page is static and exposes the embedded JSON to plain HTTP, pass `--no-browser-fallback`.

This structured example calls the reader's raw HTTP/SEO/browser fetch primitives
because the article-oriented `read_url` contract intentionally returns cleaned
text. The raw markup is parsed in memory and is never written to the repository.

## Placeholder command

Use an operator-owned placeholder URL template with a `{page}` token. Keep pagination bounded and add a delay between pages:

```bash
python examples/generic_rtx_prices.py \
  --url-template 'https://marketplace.invalid/search?q=rtx&page={page}' \
  --max-pages 3 \
  --delay-seconds 2 \
  --browser-post-load-wait-ms 8000
```

`marketplace.invalid` is a placeholder only. Replace it locally with a URL you are allowed to query; do not commit real target URLs or fetched page content.

## Embedded JSON shape

By default the example reads a non-executed JSON script isolated by id:

```html
<script id="RTX_PRICE_DATA" type="application/json">
{
  "items": [
    {"id": "synthetic-1", "title": "Generic RTX 4070", "price": "599 €", "currency": "EUR", "url": "/listing/synthetic-1"}
  ]
}
</script>
```

The script does not infer marketplace-specific state from arbitrary JavaScript. If your page uses different field names, provide a local config file:

```json
{
  "script_id": "__NEXT_DATA__",
  "items_path": ["props", "pageProps", "searchData", "ads"],
  "id_field": "list_id",
  "title_field": "subject",
  "price_field": "price_cents",
  "price_unit": "cents",
  "currency_field": "",
  "url_field": "url"
}
```

Run with `--config /path/to/local-config.json`. Do not commit a config containing real marketplace details if it reveals private targets.

For a page whose embedded records expose a major-unit price instead, use
`"price_field": "price"`, `"price_unit": "major"`, and either a per-record
`"currency_field"` or `"default_currency": "EUR"`. One-value arrays such as
`[360]` are accepted and reduced to their first scalar value.

## Procedure for the dynamic search page

1. Create the mapping above in a local file such as `/tmp/rtx-price-config.json`.
2. Put the allowed search URL in a shell variable; keep the real value out of
   Git and out of screenshots or copied logs.
3. Add the page token to the URL and run a bounded scan. For a complete result
   set, choose the page limit exposed by the target and keep a polite delay:

```bash
export SEARCH_URL='https://marketplace.invalid/search?q=rtx'
python examples/generic_rtx_prices.py \
  --url-template "${SEARCH_URL}?page={page}" \
  --config /tmp/rtx-price-config.json \
  --max-pages 100 \
  --delay-seconds 2 \
  --browser-post-load-wait-ms 8000 \
  > /tmp/rtx-prices.json
```

4. Read `status`, `count`, `pages`, and `warnings` before using the prices.
`ok` means at least one deduplicated RTX record was extracted; `partial` means
the pages were reachable but one or more records/pages were not usable; `error`
means no page could be fetched. Never treat a non-empty file alone as proof of
complete coverage. When the markup looks like an access challenge, the current
page is marked `blocked` and pagination stops; the overall result is `partial`.

5. If the page needs login, CAPTCHA solving, an acceptance click, or a control
that is not an explicit refusal/continue-without-accepting action, stop the run
and review it manually. This example does not bypass those controls.

## Output contract

The command prints JSON:

- `status: "ok"` when at least one RTX listing with an EUR price was extracted.
- `status: "partial"` when pages were fetched but no usable listing was extracted, when some pages could not expose the configured JSON, or when an access challenge stops pagination.
- `status: "error"` when all page reads failed.
- `listings[]` contains deduplicated objects: `id`, `title`, `price_eur`, `price_cents`, `page`, and optional `relative_url`.
- `pages[]` records page number, status, fetch method, and URL template expansion.
- `warnings[]` records extraction/fetch caveats.

The example filters titles containing an RTX token, converts common euro formats such as `1 234,56 €`, `EUR 1,299.00`, and configured major/cents numeric values, and deduplicates by the configured listing id.

## Dynamic pages and consent guard

With browser fallback enabled, the existing runtime follows the same HTTP → SEO → browser pipeline as the package reader. During browser rendering it may close a recognized cookie/consent window only when it finds an explicit refusal or continuation-without-accepting control, for example `Tout refuser`, `Reject all`, or `Continuer sans accepter`.

It never clicks arbitrary buttons and never clicks acceptance labels. If the page requires login, CAPTCHA solving, subscription access, or accepting tracking, stop and handle that outside this example according to the site terms.

## Limits and responsible operation

- Keep `--max-pages` bounded; the script enforces `1..100`.
- Use `--delay-seconds` to respect the target's rhythm.
- No cookies, tokens, browser profiles, fetched HTML, or live results are written by this example.
- Archive fallback is disabled for this price example to avoid mixing stale snapshots with price data.
- The script is an extraction pattern, not a bypass service. Use only on pages you are allowed to query and comply with terms, robots/rate expectations, copyright, and local law.
