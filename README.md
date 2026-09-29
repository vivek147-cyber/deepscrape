# deepscrape

A [Claude Code](https://claude.com/claude-code) skill that deep-scrapes any public website and hands back everything in one folder: a readable report, structured JSON, product CSVs, screenshots, and the hidden API calls the site uses to load its data. Built on [Scrapling](https://github.com/D4Vinci/Scrapling) and Chromium.

Just ask Claude: *"scrape example.com and get me everything, including products and contact details."*

## What you get

| Output | What it is |
|---|---|
| `report.md` | Site-wide summary + per-page details, in plain language |
| `data.json` | Every extracted field, per page |
| `contacts.csv` | Emails, phones, social profiles, and where each was found |
| `products.csv` / `.json` | Name, price, currency, brand, SKU, rating, image, URL |
| `pages/*.md` | Full visible text of every page |
| `screenshot-*.png`, `rendered-*.html` | Full-page screenshot and post-JavaScript HTML (`--full`) |
| `api/*.json` | Raw JSON API responses the page fetched (`--full`) |

Per page it extracts: metadata, OpenGraph/Twitter cards, heading outline, keywords, links, images, tables, forms, JSON-LD, tech stack (CMS, frameworks, analytics), and more. `--crawl` follows the sitemap and internal links.

## How it works

1. **Tiered fetching** starts with fast HTTP (Chrome TLS impersonation), then escalates to a real Chromium browser, then to Scrapling's stealth browser, only when needed.
2. **`--full` mode** always uses Chromium: it renders the page, scrolls and clicks "load more", takes a screenshot, records every XHR/fetch call, and mines the JSON responses for product lists. Modern shops load products from an API, so this finds data plain scraping misses.
3. **Blocked pages are reported honestly** instead of being saved as if they were content.

## Install

Requires Python 3.9+.

```bash
git clone https://github.com/vivek147-cyber/deepscrape
cd deepscrape
./install.sh            # macOS/Linux   (add --project for current project only)
.\install.ps1           # Windows PowerShell (add -Project for current project only)
```

Or manually: copy `skills/deepscrape` into `~/.claude/skills/`, then `pip install -r requirements.txt` and `scrapling install`. Restart Claude Code.

## Use without Claude

```bash
python skills/deepscrape/scripts/scrape.py https://example.com --out out
python skills/deepscrape/scripts/scrape.py https://shop.example.com --crawl --full --max-pages 20 --out out
```

| Flag | Purpose |
|---|---|
| `--crawl` | Sitemap + internal-link crawl |
| `--max-pages N` | Page cap (default 25) |
| `--full` | Chromium render, scroll, screenshot, API capture, products |
| `--products` | Product extraction (JSON-LD, then API, then card detection) |
| `--tier auto\|http\|browser\|stealth` | Force a fetch method |
| `--delay S` | Delay between requests (default 1s) |
| `--ignore-robots` | Only for sites you own or have permission to scrape |

## Responsible use

deepscrape is built to be polite and to stay within public content:

- Obeys `robots.txt` and crawl delays by default.
- Does not log in, bypass paywalls, or solve CAPTCHAs/challenges. A page that stays blocked is reported as blocked.
- Captures only the API calls the public page itself makes, and never guesses hidden endpoints or uses credentials.
- **You** are responsible for complying with each site's Terms of Service and with privacy law (GDPR, CAN-SPAM, etc.) when using contact data. Many large platforms prohibit scraping; prefer their official APIs.

## Tested on

books.toscrape.com, webscraper.io test sites, quotes.toscrape.com/js (JS-rendered), apple.com/in, nike.com/in (36 products via its category API), bbc.com, walmart.com. A Cloudflare challenge page was correctly reported as blocked.

## Contributing

Issues and PRs welcome, especially: more tech-stack signatures, better product-field mapping for API payloads, pagination of discovered APIs, and Excel export.

## License

MIT
