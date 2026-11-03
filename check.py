"""Polite Pokemon TCG stock checker for UK shops. Stdlib only.

One request per site per run. Shopify shops are read via their public
/products.json; other shops get their Pokemon page diffed for new product lines.
Alerts go to ntfy (NTFY_TOPIC), Discord (DISCORD_WEBHOOK) and/or one email
digest per run (SMTP_USER/SMTP_PASS, optional SMTP_HOST, EMAIL_TO).
Writes status.json, which dashboard.html displays.
"""
import json, os, re, smtplib, sys, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
STATUS = os.path.join(HERE, "status.json")
HISTORY = os.path.join(HERE, "history.json")
NOTIFS = os.path.join(HERE, "notifications.json")
UA = "Mozilla/5.0 (personal stock alert; one request per 15 min)"


def fetch(url, retry=True):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-GB"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code != 429 or not retry:
            raise
        time.sleep(min(int(e.headers.get("Retry-After") or 10), 10))  # rate limited: wait as asked, try once more
        return fetch(url, retry=False)


def wanted(title, cfg):
    t = title.lower()
    return any(k in t for k in cfg["include"]) and not any(k in t for k in cfg["exclude"])


def rrp_for(title, cfg):
    """UK RRP for a product title from sites.json "rrp"; the longest matching key wins (e.g. Pokemon Center ETB)."""
    t = title.lower()
    keys = [k for k in cfg["rrp"] if k in t]
    return cfg["rrp"][max(keys, key=len)] if keys else None


def over_rrp(title, price, cfg):
    rrp = rrp_for(title, cfg)
    return rrp is not None and price > rrp


def shopify_items(data, base, cfg):
    """{variant_id: (title, price, available, url)} for wanted products."""
    out = {}
    for p in data.get("products", []):
        # Shops sell other TCGs too; Pokemon stock always has Pokemon in the title or vendor.
        if not wanted(p["title"], cfg) or "pok" not in (p["title"] + p.get("vendor", "")).lower():
            continue
        for v in p.get("variants", []):
            title = p["title"] if v.get("title") in (None, "Default Title") else f'{p["title"]} ({v["title"]})'
            tags = " ".join(p["tags"]) if isinstance(p.get("tags"), list) else str(p.get("tags", ""))
            tag = DROP_WORDS.search(tags + " " + p.get("product_type", ""))
            if tag and not DROP_WORDS.search(title):  # shop marks it as a pre-order/ballot only in tags
                title += f" ({tag.group(1).title()})"
            out[str(v["id"])] = (title, float(v.get("price") or 0), bool(v.get("available")), f'{base}/products/{p["handle"]}')
    return out


def shopify_alerts(old, new, cfg):
    msgs = []
    for vid, (title, price, avail, url) in new.items():
        was = old.get(vid)
        if avail and (was is None or not was[2]):
            tag = "NEW" if was is None else "BACK IN STOCK"
            warn = " (over RRP)" if over_rrp(title, price, cfg) else ""
            # Tapping the alert opens the shop with this item already in the basket.
            msgs.append((f"{tag}: {title} £{price:.2f}{warn}", url.split("/products/")[0] + f"/cart/{vid}:1"))
    return msgs


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.links, self._skip, self._href = [], [], 0, None

    def handle_starttag(self, tag, a):
        self._skip += tag in ("script", "style")
        if tag == "a":
            self._href = dict(a).get("href")

    def handle_endtag(self, tag):
        self._skip -= tag in ("script", "style") and self._skip > 0
        if tag == "a":
            self._href = None

    def handle_data(self, d):
        if not self._skip and d.strip():
            text = re.sub(r"\s+", " ", d.strip())
            self.parts.append(text)
            if self._href:
                self.links.append((self._href, text))


def page_lines(html, cfg):
    p = _Text()
    p.feed(html)
    # Many shops (e.g. BigCommerce, Next.js) put the product list in page JSON rather than visible HTML.
    names = re.findall(r'"name":"([^"]{3,150})"', html.replace('\\"', '"'))
    # Pages mix in other TCGs and menus: keep lines naming Pokemon or a Pokemon set from releases.json.
    marks = ["pok"] + cfg.get("sets", [])
    return sorted({s[:150] for s in p.parts + names if len(s) < 200 and wanted(s, cfg) and any(m in s.lower() for m in marks)})


def page_links(html, page_url, cfg):
    """{absolute_url: title} for product links on a page."""
    p = _Text()
    p.feed(html)
    return {urljoin(page_url, h).split("#")[0]: t[:150] for h, t in p.links if len(t) < 200 and wanted(t, cfg)}


def check_drop_page(page, cfg, prev):
    """Returns (new_state, [drop dicts]). On first sight every product is returned with seeded=True:
    shown on the dashboard, but not pushed as an alert."""
    links = {u: t for u, t in page_links(fetch(page["url"]), page["url"], cfg).items()
             if re.search(r"/prod(ucts)?/", u) and "pok" in (u + t).lower()}  # product pages only, not menus
    seen = set(prev.get("links", [])) if prev else set()
    new = [{"shop": page["shop"], "name": t, "url": u, "type": page["type"], "closes": None, "seeded": not prev,
            "note": "Spotted automatically. Open it to check if it's open and when it closes."}
           for u, t in sorted(links.items()) if u not in seen]
    return {"links": sorted(seen | set(links))}, new


DROP_WORDS = re.compile(r"\b(ballot|raffle|prize draw|product drop|pre[- ]?order)", re.I)


def listing_drops(name, st, cfg):
    """{key: drop} for a shop's listings that say ballot/raffle/pre-order. Pre-orders over RRP are left out."""
    out = {}
    for vid, (title, price, avail, url) in st.get("items", {}).items():
        m = DROP_WORDS.search(title)
        if m and (not m.group(1).lower().startswith("pre") or not over_rrp(title, price, cfg)):
            out[url] = (title, url, m.group(1))
    for line in st.get("lines", []):  # page-checked shops: no price or own link, so key by title
        m = DROP_WORDS.search(line)
        if m:
            out[name + ":" + line] = (line, None, m.group(1))
    return {k: {"shop": name, "name": t, "url": u, "closes": None,
                "type": "pre-order" if w.lower().startswith("pre") else "ballot",
                "note": "Spotted in the shop's listings. Open it to check how to enter and when it closes."}
            for k, (t, u, w) in out.items()}


MONTHS = "january february march april may june july august september october november december".split()
DATE_NEAR = re.compile(r"(?:expected|release[sd]?(?: date)?|releasing|available(?: from| on)?|out on|launch(?:es)?|dispatch(?:es|ed)?(?: from| on)?)"
                       r"\W{0,20}(?:on |from )?(\d{1,2})(?:st|nd|rd|th)?[ /.-]([a-z]+|\d{1,2})[ /.,-]*(\d{4})", re.I)


def expected_date(html):
    """First 'Expected 06/11/2026' / 'Release date: 6th November 2026' style date on a page, as YYYY-MM-DD, or None."""
    text = re.sub(r"<[^>]+>", " ", html)
    for d, m, y in DATE_NEAR.findall(text):
        month = int(m) if m.isdigit() else next((i + 1 for i, n in enumerate(MONTHS) if n.startswith(m.lower()[:3])), 0)
        if 1 <= month <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{month:02d}-{int(d):02d}"
    return None


def notify(site, text, url, topic=None):
    """Sends to every configured channel; returns the names of the ones that worked."""
    print(f"[{site}] {text} {url}")
    sent = []
    if topic:
        req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=text.encode(),
                                     headers={"Title": site, "Click": url, "Tags": "rotating_light"})
        urllib.request.urlopen(req, timeout=20)
        sent.append("push")
    if os.environ.get("DISCORD_WEBHOOK"):
        body = json.dumps({"content": f"**{site}**: {text}\n{url}"}).encode()
        req = urllib.request.Request(os.environ["DISCORD_WEBHOOK"], data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": UA})
        urllib.request.urlopen(req, timeout=20)
        sent.append("discord")
    return sent


def check_site(site, cfg, prev):
    """Returns (new_state, [(text, url)]). First sight of a site seeds state silently."""
    parts = urlsplit(site["url"])
    base = f"{parts.scheme}://{parts.netloc}"
    try:
        # A /collections/ URL is read as that collection only (keeps e.g. Double Sleeved to its pre-orders).
        # /collections/all is read store-wide instead, since only the store feed lists newest first.
        src = site["url"].split("?")[0].rstrip("/") if "/collections/" in parts.path \
            and not parts.path.startswith("/collections/all") else base
        data = json.loads(fetch(src + "/products.json?limit=250"))
        if "products" in data:
            items = shopify_items(data, base, cfg)
            alerts = shopify_alerts(prev.get("items", {}), items, cfg) if prev else []
            return {"mode": "shopify", "items": items}, alerts
    except Exception as e:
        if getattr(e, "code", None) == 429:
            raise  # rate-limited Shopify: keep last state rather than flip to page mode and re-alert everything
        # otherwise not Shopify (or blocked): fall back to the page itself
    lines = page_lines(fetch(site["url"]), cfg)
    added = sorted(set(lines) - set(prev.get("lines", []))) if prev else []
    alerts = [(f"New on page: {l}", site["url"]) for l in added[:5]]
    if len(added) > 5:
        alerts.append((f"...and {len(added) - 5} more", site["url"]))
    return {"mode": "page", "lines": lines}, alerts


PRICE = re.compile(r"£(\d+(?:\.\d+)?)")


def wish_hit(text, cfg):
    """Name of the first wishlist entry whose terms are all in text and whose max price isn't exceeded."""
    t, m = text.lower(), PRICE.search(text)  # prices come from our own alert text, e.g. "£49.99"
    for w in cfg.get("wishlist", []):
        if w.get("got"):
            continue  # already got it: no more alerts
        if all(k in t for k in w["match"]) and (w.get("max") is None or not m or float(m.group(1)) <= w["max"]):
            return w["name"]
    return None


def got_it(text, cfg):
    """True if text is about a wishlist item already marked as got on the dashboard."""
    t = text.lower()
    return any(w.get("got") and all(k in t for k in w["match"]) for w in cfg.get("wishlist", []))


def drop_reminders(drops, now, reminded):
    """[(shop, text, url)] for drops closing within 6 hours that haven't been reminded yet."""
    out = []
    for d in drops:
        key = d["shop"] + d["name"] + str(d.get("closes"))
        if d.get("closes") and key not in reminded:
            hours = (datetime.fromisoformat(d["closes"]) - now).total_seconds() / 3600
            if 0 < hours <= 6:
                reminded.append(key)
                out.append((d["shop"], f"Closes in {hours:.0f}h: {d['name']}", d["url"]))
    return out


def send_email(alerts):
    """One digest email per run. alerts: [(site, text, url)]"""
    user = os.environ.get("SMTP_USER")
    if not (user and alerts):
        return
    msg = EmailMessage()
    msg["Subject"] = f"Pokemon alert: {len(alerts)} alert(s) - {alerts[0][1][:60]}"
    msg["From"], msg["To"] = user, os.environ.get("EMAIL_TO") or user
    msg.set_content("\n\n".join(f"{site}: {text}\n{url}" for site, text, url in alerts))
    with smtplib.SMTP(os.environ.get("SMTP_HOST") or "smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, os.environ["SMTP_PASS"])
        smtp.send_message(msg)


def site_status(site, st, err, cfg):
    row = {"name": site["name"], "url": site["url"], "skip": site.get("skip"), "error": err,
           "mode": st.get("mode"), "items": []}
    for vid, (title, price, avail, url) in sorted(st.get("items", {}).items(), key=lambda kv: kv[1][0]):
        # Shopify cart permalink: opens the shop with this variant already in the basket (you still check out).
        cart = url.split("/products/")[0] + f"/cart/{vid}:1"
        row["items"].append({"title": title, "price": price, "available": avail, "url": url,
                             "over_rrp": over_rrp(title, price, cfg), "rrp": rrp_for(title, cfg), "cart": cart})
    row["items"] += [{"title": l, "url": site["url"]} for l in st.get("lines", [])]
    return row


def release_window(releases, today):
    """True on the day before and the day of any release."""
    return any(0 <= (datetime.fromisoformat(r["date"]).date() - today).days <= 1 for r in releases)


def main():
    cfg = json.load(open(os.path.join(HERE, "sites.json")))
    rel_path = os.path.join(HERE, "releases.json")
    releases = json.load(open(rel_path)) if os.path.exists(rel_path) else {}
    now_dt = datetime.now(timezone.utc)
    # The workflow calls this every 5 min; outside release days only scan if the last scan is 12+ min old.
    # Elapsed time, not the clock minute: GitHub often starts scheduled runs late, which used to skip scans for hours.
    last = (json.load(open(STATUS)) if os.path.exists(STATUS) else {}).get("updated")
    if os.environ.get("GITHUB_ACTIONS") and last and now_dt - datetime.fromisoformat(last) < timedelta(minutes=12) \
            and not release_window(releases.get("releases", []), now_dt.date()):
        print("Not a release day and scanned recently: skipping this 5-minute slot")
        return
    wl_path = os.path.join(HERE, "wishlist.json")  # edited from the dashboard
    cfg["wishlist"] = json.load(open(wl_path)) if os.path.exists(wl_path) else []
    cfg["sets"] = [r["set"].lower() for r in releases.get("releases", []) if r.get("set")]
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    status = json.load(open(STATUS)) if os.path.exists(STATUS) else {}
    drops = releases.get("drops", [])
    now = now_dt.isoformat(timespec="seconds")
    rows, run_alerts, shop_drops = [], [], {}
    for site in cfg["sites"]:
        err = None
        if not site.get("skip"):
            time.sleep(4)  # most shops share Shopify's servers, which answer 429 to fast back-to-back requests (2s still drew 429s)
            try:
                state[site["name"]], alerts = check_site(site, cfg, state.get(site["name"]))
                run_alerts += [(site["name"], t, u) for t, u in alerts]
                shop_drops[site["name"]] = {k: {**d, "url": d["url"] or site["url"]}
                                            for k, d in listing_drops(site["name"], state[site["name"]], cfg).items()}
            except Exception as e:  # one broken site must not stop the rest
                err = str(e)[:200]
                print(f"[{site['name']}] check failed: {e}", file=sys.stderr)
        rows.append(site_status(site, state.get(site["name"], {}), err, cfg))
        if not site.get("skip"):  # scan success rate, a moving average over roughly the last 50 scans
            up = state.setdefault("_uptime", {})
            rows[-1]["uptime"] = up[site["name"]] = round(0.98 * up.get(site["name"], 1.0) + 0.02 * (err is None), 4)
    auto_drops = [d for d in status.get("auto_drops", [])
                  if (now_dt - datetime.fromisoformat(d["found"])).days < 30]
    # Ballots and pre-orders found in every shop's listings; a shop's first scan lists them without alerting.
    dropseen = state.setdefault("_dropseen", {})
    for shop, found in shop_drops.items():
        first = shop not in dropseen
        for k, d in found.items():
            if k in dropseen.get(shop, []):
                continue
            if not any(x["url"] == d["url"] and x["name"] == d["name"] for x in auto_drops):
                auto_drops.insert(0, {**d, "found": now})
            if not first:
                run_alerts.append((shop, f"New {d['type']} spotted: {d['name']}", d["url"]))
        dropseen[shop] = sorted(set(dropseen.get(shop, [])) | set(found))
    for page in cfg.get("drop_pages", []):
        key = "_droppage:" + page["url"]
        try:
            state[key], new = check_drop_page(page, cfg, state.get(key))
        except Exception as e:
            print(f"[{page['shop']} drops] check failed: {e}", file=sys.stderr)
            continue
        listed = {d["url"] for d in auto_drops}
        for d in new:
            if d["url"] not in listed:
                auto_drops.insert(0, {**d, "found": now})
            if not d.pop("seeded"):
                run_alerts.append((d["shop"], f"New {d['type']} spotted: {d['name']}", d["url"]))
    # Expected release date from each drop's own product page: once when found, then weekly, a few per run.
    shop_urls = {x["url"] for x in cfg["sites"]}
    stale = [d for d in auto_drops if d["url"] not in shop_urls
             and (now_dt - datetime.fromisoformat(d.get("date_checked") or "2000-01-01T00:00:00+00:00")).days >= 7]
    for d in stale[:15]:
        try:
            time.sleep(2)
            d["expected"] = expected_date(fetch(d["url"])) or d.get("expected")
        except Exception as e:
            print(f"[{d['shop']}] date check failed: {e}", file=sys.stderr)
        d["date_checked"] = now
    # With a wishlist, only wishlist hits (and drop reminders) ping you; everything still shows on the dashboard.
    push = [a for a in run_alerts if not got_it(a[1], cfg)
            and (not cfg.get("wishlist") or wish_hit(a[1], cfg) or " spotted: " in a[1])]
    reminders = [r for r in drop_reminders(drops, now_dt, state.setdefault("_reminded", [])) if not got_it(r[1], cfg)]
    push += reminders
    run_alerts += reminders
    # The public topic in sites.json is what the dashboard's "Push notifications" button subscribes people to.
    topic = cfg.get("ntfy_topic") or os.environ.get("NTFY_TOPIC")
    log = []
    for name, text, url in push:
        try:
            via = notify(name, text, url, topic)
        except Exception as e:
            via = []
            print(f"[{name}] notify failed: {e}", file=sys.stderr)
        log.append({"time": now, "site": name, "text": text, "url": url, "via": via})
    try:
        send_email(push)
        if push and os.environ.get("SMTP_USER"):
            for entry in log:
                entry["via"].append("email")
    except Exception as e:
        print(f"email failed: {e}", file=sys.stderr)
    if log:  # log of every notification sent, shown on the dashboard's Notifications tab
        old = json.load(open(NOTIFS)) if os.path.exists(NOTIFS) else []
        json.dump((log[::-1] + old)[:500], open(NOTIFS, "w"), indent=1)
    # Keep only well-formed entries: older runs stored the restock log here by mistake.
    feed = [{"time": now, "site": n, "text": t, "url": u} for n, t, u in run_alerts] + [a for a in status.get("alerts", []) if "text" in a]
    # Restock log for the dashboard's day/hour patterns.
    history = json.load(open(HISTORY)) if os.path.exists(HISTORY) else []
    # rrp: True/False for priced restocks, None when the shop's page shows no price
    history += [{"t": now, "shop": n, "rrp": None if t.startswith("New on page:") else "(over RRP)" not in t}
                for n, t, u in run_alerts if t.startswith(("NEW:", "BACK IN STOCK:", "New on page:"))]
    json.dump(history[-3000:], open(HISTORY, "w"))
    json.dump(state, open(STATE, "w"), indent=1, sort_keys=True)
    json.dump({"updated": now, "sites": rows, "alerts": feed[:100], "wishlist": cfg.get("wishlist", []),
               "auto_drops": auto_drops[:150]}, open(STATUS, "w"), indent=1)


if __name__ == "__main__":
    main()
