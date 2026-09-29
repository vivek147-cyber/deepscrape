#!/usr/bin/env python3
"""Detailed website scraper built on Scrapling.

Tiers (escalates only when the cheaper tier fails or returns an empty JS shell):
  1. Fetcher         - fast HTTP with browser TLS impersonation
  2. DynamicFetcher  - real Chromium (JS rendering, waits for network idle)
  3. StealthyFetcher - patched, fingerprint-resistant browser for bot-protected sites

Usage:
  python scrape.py https://example.com --out out_dir
  python scrape.py https://example.com --crawl --max-pages 50 --tier auto
Outputs: <out>/data.json, <out>/report.md, <out>/pages/*.md (per-page text)
"""
import argparse, json, re, sys, time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

try:
    from scrapling.fetchers import Fetcher, DynamicFetcher, StealthyFetcher
except ImportError:
    sys.exit('Scrapling missing. Run: pip install "scrapling[fetchers]" && scrapling install')

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)\d{3,4}[\s.-]?\d{3,4}(?!\d)")
SOCIAL = {
    "facebook": "facebook.com", "instagram": "instagram.com", "twitter/x": ("twitter.com", "x.com"),
    "linkedin": "linkedin.com", "youtube": "youtube.com", "tiktok": "tiktok.com",
    "pinterest": "pinterest.com", "github": "github.com", "whatsapp": ("wa.me", "whatsapp.com"),
    "telegram": "t.me",
}
TECH_SIGNATURES = {
    "WordPress": ["wp-content", "wp-includes"], "Shopify": ["cdn.shopify.com", "Shopify.theme"],
    "WooCommerce": ["woocommerce"], "Wix": ["wixstatic.com", "wix.com"], "Squarespace": ["squarespace.com"],
    "Webflow": ["webflow.com", "data-wf-page"], "Magento": ["Mage.Cookies", "/static/frontend/"],
    "React": ["data-reactroot", "__NEXT_DATA__", "react-dom"], "Next.js": ["__NEXT_DATA__", "/_next/"],
    "Vue": ["data-v-", "vue.js", "__vue__"], "Nuxt": ["__NUXT__", "/_nuxt/"], "Angular": ["ng-version", "ng-app"],
    "jQuery": ["jquery"], "Bootstrap": ["bootstrap"], "Tailwind": ["tailwind"],
    "Google Analytics": ["google-analytics.com", "gtag(", "googletagmanager.com/gtag"],
    "Google Tag Manager": ["googletagmanager.com/gtm.js"], "Facebook Pixel": ["fbevents.js", "fbq("],
    "Hotjar": ["hotjar.com"], "Cloudflare": ["cloudflare", "cf-ray"], "HubSpot": ["hs-scripts.com", "hubspot"],
    "Stripe": ["js.stripe.com"], "Razorpay": ["checkout.razorpay.com"],
}
BLOCK_MARKERS = ["just a moment", "attention required", "access denied", "verify you are human",
                 "enable javascript and cookies", "captcha", "are you a robot"]


# ---------- fetching ----------
SHOT = {"path": None}  # screenshot target for the next browser fetch (set by main in --full mode)


def _scroll_and_shoot(page):
    """Runs inside the browser: scroll to trigger lazy loading / infinite scroll, then screenshot."""
    last = 0
    for _ in range(25):
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(500)
        h = page.evaluate("document.body.scrollHeight")
        if h == last:
            break
        last = h
    for label in ("load more", "show more", "view more"):
        try:
            btn = page.get_by_role("button", name=re.compile(label, re.I))
            for _ in range(5):
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=2000); page.wait_for_timeout(1200)
                else:
                    break
        except Exception:
            pass
    page.wait_for_timeout(800)
    if SHOT["path"]:
        try:
            page.screenshot(path=SHOT["path"], full_page=True)
        except Exception:
            pass
    return page


def _fetch(url, tier, timeout, full=False):
    extra = dict(capture_xhr=r".*", page_action=_scroll_and_shoot) if full else {}
    if tier == "browser":
        return DynamicFetcher.fetch(url, headless=True, network_idle=True, timeout=timeout * 1000, **extra)
    if tier == "stealth":
        return StealthyFetcher.fetch(url, headless=True, network_idle=True, timeout=timeout * 1000, **extra)
    if tier == "http":
        return Fetcher.get(url, stealthy_headers=True, impersonate="chrome", timeout=timeout, follow_redirects=True)
    if tier == "browser":
        return DynamicFetcher.fetch(url, headless=True, network_idle=True, timeout=timeout * 1000)
    if tier == "stealth":
        return StealthyFetcher.fetch(url, headless=True, network_idle=True, timeout=timeout * 1000)
    raise ValueError(tier)


def _text_len(page):
    try:
        return len(page.get_all_text(ignore_tags=("script", "style", "noscript")).strip())
    except Exception:
        return 0


def _looks_blocked_or_empty(page):
    status = getattr(page, "status", 200)
    if status in (401, 403, 429, 503):
        return f"status {status}"
    body = _page_html(page).lower()
    head = body[:6000]
    if any(m in head for m in BLOCK_MARKERS) and _text_len(page) < 1500:
        return "bot-challenge page"
    if _text_len(page) < 200 and ("<script" in body):
        return "empty JS shell"
    return None


def fetch_page(url, tier="auto", timeout=30, full=False):
    """Return (page, tier_used, notes). Escalates http -> browser -> stealth in auto mode.
    full=True skips the HTTP tier: a real Chromium session renders, scrolls, screenshots and captures API calls."""
    order = (["browser", "stealth"] if full else ["http", "browser", "stealth"]) if tier == "auto" else [tier]
    if full and tier == "http":
        order = ["browser"]
    notes = []
    page = None
    for t in order:
        try:
            page = _fetch(url, t, timeout, full)
        except Exception as e:  # network/browser errors -> try next tier
            notes.append(f"{t}: error {type(e).__name__}: {str(e)[:120]}")
            continue
        problem = _looks_blocked_or_empty(page)
        if not problem:
            return page, t, notes
        notes.append(f"{t}: {problem}")
    # every tier failed or was blocked: caller decides how to record it
    return page, (order[-1] if page is not None else None), notes + ["BLOCKED"]


def _page_html(page):
    for attr in ("html_content", "body"):
        v = getattr(page, attr, None)
        if v:
            return v.decode("utf-8", "ignore") if isinstance(v, bytes) else str(v)
    return str(page)


# ---------- extraction ----------
def _abs(base, href):
    if not href:
        return None
    href = href.strip()
    if href.startswith(("javascript:", "#", "data:")):
        return None
    return urldefrag(urljoin(base, href))[0]


def _meta(page, sel_attr):
    out = {}
    for m in page.css("meta"):
        key = m.attrib.get("property") or m.attrib.get("name") or m.attrib.get("http-equiv")
        if key and m.attrib.get("content") is not None:
            out[key] = m.attrib["content"]
    return out


def extract(page, url, tier_used):
    html = _page_html(page)
    host = urlparse(url).netloc.lower()
    text = page.get_all_text(ignore_tags=("script", "style", "noscript")).strip()
    metas = _meta(page, None)

    title = (page.css("title::text").get() or "").strip()
    canonical = page.css('link[rel="canonical"]::attr(href)').get()
    lang = page.css("html::attr(lang)").get()
    favicon = page.css('link[rel~="icon"]::attr(href)').get()

    headings = {f"h{i}": [h.get_all_text().strip() for h in page.css(f"h{i}") if h.get_all_text().strip()]
                for i in range(1, 7)}
    headings = {k: v for k, v in headings.items() if v}

    links_internal, links_external, mailtos, tels = [], [], [], []
    for a in page.css("a[href]"):
        href = a.attrib.get("href", "")
        label = a.get_all_text().strip()[:120]
        if href.startswith("mailto:"):
            mailtos.append(href[7:].split("?")[0]); continue
        if href.startswith("tel:"):
            tels.append(href[4:]); continue
        full = _abs(url, href)
        if not full or not full.startswith("http"):
            continue
        rec = {"url": full, "text": label}
        (links_internal if urlparse(full).netloc.lower() == host else links_external).append(rec)

    def dedupe(recs):
        seen, out = set(), []
        for r in recs:
            if r["url"] not in seen:
                seen.add(r["url"]); out.append(r)
        return out
    links_internal, links_external = dedupe(links_internal), dedupe(links_external)

    images = []
    for im in page.css("img"):
        src = im.attrib.get("src") or im.attrib.get("data-src") or im.attrib.get("data-lazy-src")
        full = _abs(url, src)
        if full:
            images.append({"src": full, "alt": im.attrib.get("alt", ""),
                           "width": im.attrib.get("width"), "height": im.attrib.get("height")})

    tables = []
    for t in page.css("table"):
        rows = []
        for tr in t.css("tr"):
            cells = [c.get_all_text().strip() for c in tr.css("th, td")]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append({"headers": rows[0], "rows": rows[1:]})

    forms = []
    for f in page.css("form"):
        forms.append({"action": _abs(url, f.attrib.get("action", "")) or url,
                      "method": f.attrib.get("method", "get").upper(),
                      "fields": [{"name": i.attrib.get("name"), "type": i.attrib.get("type", i.tag),
                                  "placeholder": i.attrib.get("placeholder")}
                                 for i in f.css("input, select, textarea") if i.attrib.get("name")]})

    json_ld = []
    for s in page.css('script[type="application/ld+json"]'):
        raw = (s.text or s.get_all_text() or "").strip()
        try:
            json_ld.append(json.loads(raw))
        except Exception:
            pass

    scripts = [_abs(url, s.attrib.get("src")) for s in page.css("script[src]")]
    lowered = html.lower()
    tech = sorted({name for name, sigs in TECH_SIGNATURES.items() if any(sg.lower() in lowered for sg in sigs)})
    generator = metas.get("generator")
    if generator:
        tech.append(f"generator: {generator}")

    emails = sorted({e.lower() for e in EMAIL_RE.findall(text + " " + html)
                     if not e.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".js", ".css"))}
                    | {m.lower() for m in mailtos})
    phones = sorted({re.sub(r"\s+", " ", p).strip() for p in PHONE_RE.findall(text)
                     if 9 <= len(re.sub(r"\D", "", p)) <= 15} | set(tels))

    social = {}
    for rec in links_external:
        d = urlparse(rec["url"]).netloc.lower().removeprefix("www.")
        for name, doms in SOCIAL.items():
            doms = (doms,) if isinstance(doms, str) else doms
            if any(d == x or d.endswith("." + x) for x in doms):
                social.setdefault(name, [])
                if rec["url"] not in social[name]:
                    social[name].append(rec["url"])

    words = re.findall(r"[A-Za-z][A-Za-z'-]{3,}", text.lower())
    stop = set("this that with from have your will about more they their what when which there been were would could also into than them then some other only over such just like most very much many each".split())
    keywords = [w for w, _ in Counter(w for w in words if w not in stop).most_common(25)]

    return {
        "url": url, "fetched_at": datetime.now(timezone.utc).isoformat(), "tier_used": tier_used,
        "status": getattr(page, "status", None), "title": title,
        "description": metas.get("description") or metas.get("og:description"),
        "canonical": canonical, "language": lang, "favicon": _abs(url, favicon),
        "meta": metas,
        "open_graph": {k: v for k, v in metas.items() if k.startswith("og:")},
        "twitter_card": {k: v for k, v in metas.items() if k.startswith("twitter:")},
        "headings": headings,
        "word_count": len(text.split()), "top_keywords": keywords,
        "contacts": {"emails": emails, "phones": phones},
        "social_links": social,
        "links": {"internal": links_internal, "external": links_external},
        "images": images, "tables": tables, "forms": forms,
        "structured_data_json_ld": json_ld,
        "technologies": tech, "script_sources": [s for s in scripts if s][:40],
        "text": text,
    }


# ---------- robots / sitemap / crawl ----------
def load_robots(base):
    rp = RobotFileParser()
    rp.set_url(urljoin(base, "/robots.txt"))
    try:
        rp.read()
    except Exception:
        return None
    return rp


def sitemap_urls(base, rp, limit=500):
    cands = list(rp.site_maps() or []) if rp else []
    cands = cands or [urljoin(base, "/sitemap.xml")]
    found, seen = [], set()
    while cands and len(found) < limit:
        sm = cands.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            r = Fetcher.get(sm, stealthy_headers=True, timeout=20)
            xml = _page_html(r)
        except Exception:
            continue
        locs = re.findall(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", xml, re.S)
        for loc in locs:
            (cands if loc.endswith(".xml") or "sitemap" in loc.lower() and loc.endswith(".xml.gz") else found).append(loc)
    return found[:limit]


def slug(url):
    p = urlparse(url)
    s = re.sub(r"[^A-Za-z0-9]+", "-", (p.path.strip("/") or "home") + ("-" + p.query if p.query else ""))
    return s[:80].strip("-") or "home"


# ---------- products ----------
PRICE_RE = re.compile(r"(?:₹|Rs\.?|INR|\$|USD|€|EUR|£|GBP)\s?\d[\d,]*(?:\.\d{1,2})?|\d[\d,]*(?:\.\d{1,2})?\s?(?:₹|€|£|USD|EUR)")


def _walk_ld(node):
    if isinstance(node, list):
        for n in node:
            yield from _walk_ld(n)
    elif isinstance(node, dict):
        yield node
        for v in node.values():
            if isinstance(v, (dict, list)):
                yield from _walk_ld(v)


def extract_products(page, rec):
    """Products from JSON-LD first (most reliable), then heuristic price+link cards."""
    url, out, seen = rec["url"], [], set()

    def add(p):
        k = (p.get("url") or "", p.get("name") or "", p.get("price") or "")
        if p.get("name") and k not in seen:
            seen.add(k); p["page"] = url; out.append(p)

    for node in _walk_ld(rec["structured_data_json_ld"]):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "Product" in types:
            offer = node.get("offers") or {}
            offer = offer[0] if isinstance(offer, list) and offer else offer
            offer = offer if isinstance(offer, dict) else {}
            img = node.get("image")
            img = img[0] if isinstance(img, list) and img else img
            brand = node.get("brand")
            rating = node.get("aggregateRating") or {}
            add({"name": node.get("name"), "price": str(offer.get("price") or offer.get("lowPrice") or "") or None,
                 "currency": offer.get("priceCurrency"), "availability": str(offer.get("availability", "")).split("/")[-1] or None,
                 "brand": brand.get("name") if isinstance(brand, dict) else brand, "sku": node.get("sku"),
                 "description": (node.get("description") or "")[:300], "image": img if isinstance(img, str) else None,
                 "rating": rating.get("ratingValue"), "reviews": rating.get("reviewCount"),
                 "url": _abs(url, node.get("url") or offer.get("url")) or url, "source": "json-ld"})

    if out:
        return out
    cands = []
    for el in page.css("li, article, div, tr")[:4000]:
        txt = el.get_all_text().strip()
        if not txt or len(txt) > 600 or len(PRICE_RE.findall(txt)) != 1:
            continue
        a = el.css("a[href]")
        if not a:
            continue
        cands.append((el, txt, el.html_content))
    htmls = [c[2] for c in cands]
    for i, (el, txt, h) in enumerate(cands[:400]):
        if any(j != i and htmls[j] in h and htmls[j] != h for j in range(len(cands))):
            continue  # keep the innermost card only
        name = None
        # title attributes are usually untruncated; headings can be price rows, so reject price-looking text
        for sel in ("a[title]", "[class*=title]", "[class*=name]", "h1", "h2", "h3", "h4", "h5", "a"):
            for n in el.css(sel):
                cand = re.sub(r"\s+", " ", (n.attrib.get("title") or n.get_all_text()).strip())
                if cand and not PRICE_RE.fullmatch(cand):
                    name = cand
                    break
            if name:
                break
        img = el.css("img")
        im = img[0].attrib.get("src") or img[0].attrib.get("data-src") if img else None
        m = PRICE_RE.search(txt)
        add({"name": name, "price": m.group(0).strip() if m else None, "currency": None, "availability": None,
             "brand": None, "sku": None, "description": None, "image": _abs(url, im),
             "rating": None, "reviews": None, "url": _abs(url, el.css("a[href]")[0].attrib.get("href")) or url,
             "source": "heuristic"})
    return out


def write_csv(path, rows, fields):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig so Excel opens it correctly
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)



# ---------- API capture (XHR/fetch calls the page made) ----------
TRACKERS = ("doubleclick.net", "google-analytics.com", "googleadservices.com", "google.com/ccm", "google.com/rmkt",
            "analytics.google.com", "facebook.com/tr", "connect.facebook.net", "hotjar.com", "clarity.ms",
            "googletagmanager.com", "segment.io", "sentry.io", "newrelic.com", "bat.bing.com", "tiktok.com/api")
NAME_KEYS = ("name", "title", "productname", "product_name", "displayname", "product_title", "label")
PRICE_KEYS = ("price", "saleprice", "sellingprice", "mrp", "regularprice", "finalprice", "currentprice",
              "offerprice", "listprice", "amount", "pricing", "priceinfo", "prices", "offers")


def _price_value(v):
    if isinstance(v, (int, float, str)):
        return str(v)
    if isinstance(v, dict):
        for k in ("value", "amount", "current", "currentPrice", "final", "sale", "min", "price", "formatted", "display"):
            if k in v and isinstance(v[k], (int, float, str)):
                return str(v[k])
        for x in v.values():
            r = _price_value(x)
            if r:
                return r
    if isinstance(v, list) and v:
        return _price_value(v[0])
    return None


def _find_item_lists(node, path="$"):
    """Yield (path, list_of_dicts) where dicts look like products: a name-ish key AND a price-ish key."""
    if isinstance(node, list):
        dicts = [x for x in node if isinstance(x, dict)]
        if dicts:
            hits = 0
            for d in dicts[:10]:
                low = {k.lower() for k in d}
                if low & set(NAME_KEYS) and low & set(PRICE_KEYS):
                    hits += 1
            if hits >= max(1, min(len(dicts), 10) // 2):
                yield path, dicts
        for i, x in enumerate(node[:50]):
            yield from _find_item_lists(x, f"{path}[{i}]")
    elif isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                yield from _find_item_lists(v, f"{path}.{k}")


def _pick(d, keys):
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        if k in low and low[k] not in (None, ""):
            return low[k]
    return None


def process_api_calls(page, page_url, out_dir):
    """Save every captured XHR/fetch call and mine JSON payloads for product records."""
    calls, products = [], []
    api_dir = out_dir / "api"
    for i, r in enumerate(getattr(page, "captured_xhr", None) or []):
        try:
            body = r.body if isinstance(r.body, (bytes, str)) else b""
            body = body.decode("utf-8", "ignore") if isinstance(body, bytes) else body
        except Exception:
            body = ""
        ctype = (getattr(r, "headers", {}) or {}).get("content-type", "")
        tracker = any(t in r.url for t in TRACKERS)
        call = {"tracker": tracker, "url": r.url, "status": getattr(r, "status", None), "method": getattr(r, "method", None),
                "content_type": ctype, "bytes": len(body), "json": False}
        data = None
        if not tracker and body.lstrip()[:1] in "{[":
            try:
                data = json.loads(body); call["json"] = True
            except Exception:
                pass
        if data is not None:
            api_dir.mkdir(exist_ok=True)
            fname = f"{i+1:03d}-{slug(r.url)[:50]}.json"
            (api_dir / fname).write_text(json.dumps(data, indent=2, ensure_ascii=False)[:5_000_000], encoding="utf-8")
            call["saved_as"] = f"api/{fname}"
            for path, items in _find_item_lists(data):
                call.setdefault("product_lists", []).append({"path": path, "count": len(items)})
                for d in items:
                    name = _pick(d, NAME_KEYS)
                    if not isinstance(name, str):
                        continue
                    img = _pick(d, ("image", "imageurl", "image_url", "thumbnail", "img", "images"))
                    if isinstance(img, list) and img:
                        img = img[0]
                    if isinstance(img, dict):
                        img = img.get("url") or img.get("src")
                    link = _pick(d, ("url", "link", "producturl", "product_url", "permalink", "slug", "handle"))
                    cur_q = re.search(r"[?&]currency=([A-Za-z]{3})", r.url)
                    products.append({
                        "name": name, "price": _price_value(_pick(d, PRICE_KEYS)),
                        "currency": _pick(d, ("currency", "currencycode", "pricecurrency")) or (cur_q.group(1).upper() if cur_q else None),
                        "availability": _pick(d, ("availability", "instock", "in_stock", "stock", "available")),
                        "brand": (lambda b: b.get("name") if isinstance(b, dict) else b)(_pick(d, ("brand", "brandname", "vendor"))),
                        "sku": _pick(d, ("sku", "id", "productid", "product_id")),
                        "rating": _pick(d, ("rating", "averagerating", "ratingvalue", "stars")),
                        "reviews": _pick(d, ("reviews", "reviewcount", "numreviews", "ratingcount")),
                        "description": str(_pick(d, ("description", "shortdescription", "summary")) or "")[:300] or None,
                        "image": _abs(page_url, img) if isinstance(img, str) else None,
                        "url": _abs(page_url, link) if isinstance(link, str) else page_url,
                        "page": page_url, "source": f"api:{r.url[:120]}",
                    })
        calls.append(call)
    return calls, products


# ---------- report ----------
def write_report(site, pages, out_dir):
    L = [f"# Scrape report: {site['domain']}", "",
         f"- Scraped: {site['scraped_at']}", f"- Pages scraped: {len(pages)}",
         f"- robots.txt respected: {site['robots_respected']}", ""]
    if site.get("skipped_by_robots"):
        L += [f"- Skipped (disallowed by robots.txt): {len(site['skipped_by_robots'])}", ""]
    if site.get("failed"):
        L += ["## Blocked / failed pages", ""]
        L += [f"- {f['url']}: {'; '.join(f['notes']) or 'no response'}" for f in site["failed"]]
        L += ["", "_These sites refused automated access. Consider an official API/data export or ask the site owner._", ""]
    agg_e = sorted({e for p in pages for e in p["contacts"]["emails"]})
    agg_p = sorted({e for p in pages for e in p["contacts"]["phones"]})
    agg_t = sorted({t for p in pages for t in p["technologies"]})
    agg_s = {}
    for p in pages:
        for k, v in p["social_links"].items():
            agg_s.setdefault(k, set()).update(v)
    L += ["## Site-wide summary", "",
          f"**Emails:** {', '.join(agg_e) or 'none found'}", "",
          f"**Phones:** {', '.join(agg_p) or 'none found'}", "",
          f"**Technologies:** {', '.join(agg_t) or 'none detected'}", "", "**Social profiles:**"]
    L += [f"- {k}: {', '.join(sorted(v))}" for k, v in agg_s.items()] or ["- none found"]
    L.append("")
    for p in pages:
        L += [f"## {p['title'] or p['url']}", "", f"- URL: {p['url']}", f"- Tier: {p['tier_used']} (HTTP {p['status']})",
              f"- Language: {p['language']}  |  Words: {p['word_count']}",
              f"- Description: {p['description'] or 'n/a'}", f"- Canonical: {p['canonical'] or 'n/a'}", ""]
        if p["headings"]:
            L.append("**Headings**")
            for lvl, hs in p["headings"].items():
                L += [f"- {lvl}: {h}" for h in hs[:15]]
            L.append("")
        L += [f"**Top keywords:** {', '.join(p['top_keywords'][:15])}", "",
              f"**Links:** {len(p['links']['internal'])} internal, {len(p['links']['external'])} external  |  "
              f"**Images:** {len(p['images'])}  |  **Tables:** {len(p['tables'])}  |  **Forms:** {len(p['forms'])}", ""]
        for t in p["tables"][:3]:
            L += ["| " + " | ".join(t["headers"]) + " |", "|" + "---|" * len(t["headers"])]
            L += ["| " + " | ".join(r + [""] * (len(t["headers"]) - len(r))) + " |" for r in t["rows"][:10]]
            L.append("")
        if p["structured_data_json_ld"]:
            types = []
            for d in p["structured_data_json_ld"]:
                for n in (d if isinstance(d, list) else d.get("@graph", [d]) if isinstance(d, dict) else []):
                    if isinstance(n, dict) and n.get("@type"):
                        types.append(str(n["@type"]))
            L += [f"**Structured data types:** {', '.join(sorted(set(types))) or 'present'}", ""]
        if p.get("api_calls") is not None:
            data_calls = [c for c in p["api_calls"] if c["json"]]
            L += ["**Browser capture (full mode)**", "",
                  f"- Rendered HTML: `{p.get('rendered_html')}`  |  Screenshot: `{p.get('screenshot') or 'n/a'}`",
                  f"- Network calls captured: {len(p['api_calls'])} ({sum(c['tracker'] for c in p['api_calls'])} trackers ignored), "
                  f"{len(data_calls)} JSON API responses saved in `api/`"]
            for c in data_calls[:15]:
                pl = c.get("product_lists")
                L.append(f"  - `{c['url'][:110]}` ({c['bytes']} bytes)" +
                         (f" -> **product list** {pl[0]['path']} ({pl[0]['count']} items)" if pl else ""))
            L.append("")
        excerpt = p["text"][:1500].replace("\n", " ")
        L += ["**Text excerpt:**", "", f"> {excerpt}{'…' if len(p['text']) > 1500 else ''}", ""]
    (out_dir / "report.md").write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--out", default="scrape_output")
    ap.add_argument("--tier", choices=["auto", "http", "browser", "stealth"], default="auto")
    ap.add_argument("--crawl", action="store_true", help="follow internal links (BFS) + sitemap")
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--ignore-robots", action="store_true", help="only for sites you own / have permission for")
    ap.add_argument("--no-text-files", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="full Chromium scrape: render, scroll/load-more, screenshot, save rendered HTML, capture API calls")
    ap.add_argument("--products", action="store_true", help="also extract product listings -> products.json/.csv")
    a = ap.parse_args()

    from pathlib import Path
    out = Path(a.out); (out / "pages").mkdir(parents=True, exist_ok=True)
    start = a.url if a.url.startswith("http") else "https://" + a.url
    base = f"{urlparse(start).scheme}://{urlparse(start).netloc}"
    host = urlparse(start).netloc.lower()
    rp = None if a.ignore_robots else load_robots(base)
    ua = "*"

    def allowed(u):
        return True if rp is None else rp.can_fetch(ua, u)

    delay = max(a.delay, rp.crawl_delay(ua) or 0) if rp and rp.crawl_delay(ua) else a.delay
    queue, seen, pages, skipped, failed = [start], set(), [], [], []
    if a.crawl:
        for u in sitemap_urls(base, rp):
            if urlparse(u).netloc.lower() == host:
                queue.append(u)

    while queue and len(pages) < a.max_pages:
        u = queue.pop(0)
        key = u.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        if not allowed(u):
            skipped.append(u); print(f"[robots] skip {u}"); continue
        print(f"[{len(pages)+1}/{a.max_pages}] {u}", flush=True)
        if a.full:
            SHOT["path"] = str(out / f"screenshot-{len(pages)+1:03d}.png")
        page, tier, notes = fetch_page(u, a.tier, a.timeout, a.full)
        if page is None or "BLOCKED" in notes:
            # don't present a challenge page as real content; log it so the report is honest
            failed.append({"url": u, "notes": [n for n in notes if n != "BLOCKED"]})
            print("   BLOCKED/failed (not saved as content):", notes); continue
        rec = extract(page, u, tier)
        rec["fetch_notes"] = notes
        if a.full:
            n = len(pages) + 1
            (out / f"rendered-{n:03d}.html").write_text(_page_html(page), encoding="utf-8")
            rec["screenshot"] = f"screenshot-{n:03d}.png" if (out / f"screenshot-{n:03d}.png").exists() else None
            rec["rendered_html"] = f"rendered-{n:03d}.html"
            rec["cookies"] = [{"name": c.get("name"), "domain": c.get("domain")} for c in (getattr(page, "cookies", None) or [])]
            rec["response_headers"] = dict(getattr(page, "headers", {}) or {})
            calls, api_products = process_api_calls(page, u, out)
            rec["api_calls"] = calls
            print(f"   api calls captured: {len(calls)} ({sum(c['json'] for c in calls)} JSON), api products: {len(api_products)}")
        if a.products or a.full:
            rec["products"] = extract_products(page, rec)
            if a.full and api_products:
                structured = [p for p in rec["products"] if p["source"] == "json-ld"]
                have = {(p['name'], p['price']) for p in structured}
                # API/JSON-LD data is cleaner than scraped cards, so drop heuristic guesses when we have it
                rec["products"] = structured + [p for p in api_products if (p['name'], p['price']) not in have]
            print(f"   products: {len(rec['products'])}")
        pages.append(rec)
        if not a.no_text_files:
            (out / "pages" / f"{len(pages):03d}-{slug(u)}.md").write_text(
                f"# {rec['title']}\n\n{u}\n\n{rec['text']}\n", encoding="utf-8")
        if a.crawl:
            for l in rec["links"]["internal"]:
                if l["url"].rstrip("/") not in seen and not re.search(
                        r"\.(pdf|zip|jpg|jpeg|png|gif|svg|webp|mp4|mp3|css|js|xml|docx?|xlsx?)(\?|$)", l["url"], re.I):
                    queue.append(l["url"])
        time.sleep(delay)

    site = {"domain": host, "start_url": start, "scraped_at": datetime.now(timezone.utc).isoformat(),
            "robots_respected": rp is not None, "skipped_by_robots": skipped, "failed": failed}
    (out / "data.json").write_text(json.dumps({"site": site, "pages": pages}, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(site, pages, out)
    contacts = ([{"type": "email", "value": e, "found_on": p["url"]} for p in pages for e in p["contacts"]["emails"]] +
                [{"type": "phone", "value": e, "found_on": p["url"]} for p in pages for e in p["contacts"]["phones"]] +
                [{"type": f"social:{k}", "value": v, "found_on": p["url"]}
                 for p in pages for k, vs in p["social_links"].items() for v in vs])
    write_csv(out / "contacts.csv", contacts, ["type", "value", "found_on"])
    if a.products or a.full:
        prods = [pr for p in pages for pr in p.get("products", [])]
        (out / "products.json").write_text(json.dumps(prods, indent=2, ensure_ascii=False), encoding="utf-8")
        write_csv(out / "products.csv", prods, ["name", "price", "currency", "availability", "brand", "sku", "rating",
                                                "reviews", "description", "image", "url", "page", "source"])
        print(f"Products: {len(prods)} -> products.csv / products.json")
    print(f"\nDone: {len(pages)} page(s) -> {out.resolve()}  (data.json, report.md, pages/)")


if __name__ == "__main__":
    main()
