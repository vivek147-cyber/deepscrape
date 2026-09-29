---
name: deepscrape
description: Deep-scrape any website (static, JavaScript-rendered, or API-driven) with Scrapling and Chromium, and return complete, detailed data plus a descriptive Markdown report. Captures page content, metadata, headings, links, images, tables, forms, emails, phones, social profiles, JSON-LD/OpenGraph, tech stack, sitemap crawl, product listings, and the hidden JSON API calls a site uses to load products. Use this whenever the user wants to scrape, crawl, extract, harvest, or pull data from a website or URL, do competitor/product/price research, build a lead list, or asks "get me everything on this site", "what API does this site use", or "find contact details for these companies" - even if they never say "scrape".
---

# deepscrape

Scrapes a URL (or a whole site) with [Scrapling](https://github.com/D4Vinci/Scrapling) and writes everything to a folder. Script path below is relative to this skill's folder.

## Quick start

```bash
python scripts/scrape.py <url> --out <dir>                       # fast: one page
python scripts/scrape.py <url> --crawl --max-pages 50 --out <dir> # whole site (sitemap + links)
python scripts/scrape.py <url> --full --out <dir>                 # FULL: Chromium render + scroll + screenshot + API capture
python scripts/scrape.py <url> --crawl --full --products --max-pages 20 --out <dir>
```

Setup once: `pip install -r requirements.txt` then `scrapling install` (downloads browsers). On Windows set `PYTHONIOENCODING=utf-8`. Scrapling prints INFO logs to stderr, and a "Response body is not available" line in `--full` mode is harmless.

## Which mode to use

| Situation | Use |
|---|---|
| Static/brochure site, blog, contact pages | default (auto tier) |
| Shop, listing, dashboard, or anything that loads after page load | `--full` |
| User wants "everything", or products/prices are missing with default mode | `--full --products` |
| Unsure | Start default; if text is thin or products are missing, rerun with `--full` |

**Why `--full` finds more:** modern shops load product data from a JSON API after the page opens, and the visible HTML is only a rendering of it. `--full` runs real Chromium, scrolls to trigger lazy loading, clicks "load more" buttons, records every XHR/fetch call the page makes, saves the JSON responses, and mines them for product records. API data is cleaner and more complete than scraping cards (for example Nike India: 36 products with SKU, price, currency from one API call), so it replaces card heuristics when present.

## Tiers (default `--tier auto`)

1. `http`: Scrapling `Fetcher`, Chrome TLS impersonation. Fast.
2. `browser`: `DynamicFetcher` (Chromium, network-idle). JS apps.
3. `stealth`: `StealthyFetcher`. Fingerprint-resistant browser for sites that block plain automation.

Auto starts cheap and escalates on blocks (401/403/429/503, challenge pages) or empty JS shells. `--full` skips `http`. Each page records `tier_used` and `fetch_notes`.

## Output folder (the "full scrape details" for the user)

| File | Contents |
|---|---|
| `report.md` | Human-readable summary: site-wide emails/phones/socials/tech, per-page headings, keywords, tables, structured data, API calls, excerpts, blocked pages |
| `data.json` | Everything, machine-readable (all fields below, per page) |
| `contacts.csv` | Emails, phones, social links + page found on |
| `products.csv` / `.json` | (`--products` or `--full`) name, price, currency, availability, brand, SKU, rating, reviews, image, URL, source (`json-ld`, `api:<endpoint>`, or `heuristic`) |
| `pages/*.md` | Full visible text of each page |
| `rendered-NNN.html`, `screenshot-NNN.png` | (`--full`) post-JavaScript HTML and full-page screenshot |
| `api/*.json` | (`--full`) raw JSON of every non-tracker API response; `data.json` lists all calls, status, size, and which held product lists |

Per page in `data.json`: title, description, canonical, language, favicon, all meta tags, OpenGraph/Twitter card, heading outline, word count, top keywords, emails, phones, social links, internal/external links with anchor text, images (src/alt/size, lazy-load aware), tables, forms, JSON-LD, detected technologies, script sources, full text, and in `--full`: response headers, cookie names, API calls.

## Options

| Flag | Purpose |
|---|---|
| `--crawl` | Sitemap + internal-link BFS crawl |
| `--max-pages N` | Page cap (default 25) |
| `--full` | Chromium render, scroll, screenshot, API capture |
| `--products` | Product extraction (implied by `--full`) |
| `--tier auto/http/browser/stealth` | Force a fetch method |
| `--delay S` | Pause between requests (default 1s; raised to robots `Crawl-delay`) |
| `--timeout S` | Per-request timeout |
| `--no-text-files` | Skip `pages/*.md` |
| `--ignore-robots` | Only for sites the user owns or has permission to scrape |

## Workflow for Claude

1. Confirm URL(s), one page vs. whole site, and whether products/prices matter. For large sites sample with a small `--max-pages` first.
2. Run into a per-site folder (e.g. `scrapes/<domain>/`).
3. Read `report.md`, skim `data.json`/`products.csv`. Summarize findings for the user (counts, contacts, products, API endpoints found), then point to the files. Don't paste raw JSON.
4. For a product listing the user wants in full: check `api_calls[].product_lists` in `data.json`. If an endpoint returns a product list, tell the user the endpoint and note it can be paged (`page`, `offset`, `PageSize` params) to fetch the whole catalog; fetch further pages with Scrapling `Fetcher.get(api_url, ...)` at a polite rate.
5. Custom fields the generic extractor misses: write targeted selectors:

```python
from scrapling.fetchers import Fetcher, DynamicFetcher
page = Fetcher.get(url, impersonate="chrome", stealthy_headers=True)   # or DynamicFetcher.fetch(url, network_idle=True)
for card in page.css(".product"):
    print(card.css(".title::text").get(), card.css(".price::text").get(), card.css("a::attr(href)").get())
```
   For big concurrent crawls use Scrapling's `Spider` (concurrency, pause/resume, `robots_txt_obey`).
6. Report gaps honestly: `site.skipped_by_robots`, and `site.failed` / "Blocked / failed pages" in the report.

## Boundaries (why they matter)

- **robots.txt is honored by default.** Disallowed URLs are listed, not fetched. Only use `--ignore-robots` for the user's own properties or with permission.
- **No login walls, paywalls or CAPTCHA/challenge solving.** Public content only. A page blocked on every tier is recorded as blocked, never saved as if it were content; suggest an official API, data export, or asking the site owner.
- **API capture reads what the public page itself requests.** It doesn't guess hidden endpoints or use credentials. Don't hammer a discovered API; use small pages and delays.
- **Personal data:** business emails/phones are fine for legitimate use, but GDPR/CAN-SPAM/local rules apply to outreach; don't profile private individuals.
- Big platforms (LinkedIn, Amazon, Instagram, etc.) prohibit scraping in their ToS and block hard; mention this and prefer official APIs.
- Keep delay >= 1s. Hammering a small site can take it down and gets your IP blocked, which ends the job anyway.

## Troubleshooting

- `Scrapling missing`: run setup above. Browser errors: `scrapling install --force`.
- Empty text or missing products: rerun with `--full`.
- Blocked on `stealth`: the site refuses automation; report it.
- `data.json` too big for context: read `report.md`, or load selected keys with Python.
