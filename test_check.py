"""Self-check against a fake local shop: python test_check.py"""
import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import check

CFG = {"include": ["elite trainer", "booster bundle"], "exclude": ["sleeves"], "rrp": {"elite trainer": 49.99}}
SHOP = {"products": []}
PAGE = {"html": ""}
PATHS = []


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        PATHS.append(self.path)
        if "/products.json" in self.path and self.server.server_port == SHOPIFY_PORT:
            body = json.dumps(SHOP).encode()
        elif self.path.startswith("/products.json"):
            self.send_error(404); return
        else:
            body = PAGE["html"].encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


def serve():
    s = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s.server_port


SHOPIFY_PORT = serve()
PAGE_PORT = serve()


def product(pid, title, price, avail, vendor="Pokemon"):
    return {"title": title, "vendor": vendor, "handle": f"p{pid}", "variants": [{"id": pid, "title": "Default Title", "price": str(price), "available": avail}]}


# Shopify: first run seeds silently
site = {"name": "s", "url": f"http://127.0.0.1:{SHOPIFY_PORT}/collections/x"}
SHOP["products"] = [product(1, "Delta Reign Elite Trainer Box", "49.99", False), product(2, "Pokemon Sleeves", "5", True),
                    product(5, "Lorcana Booster Bundle", "24.99", True, "Ravensburger")]  # other TCGs are ignored
st, alerts = check.check_site(site, CFG, None)
assert st["mode"] == "shopify" and alerts == [] and list(st["items"]) == ["1"], (st, alerts)
assert PATHS[-1] == "/collections/x/products.json?limit=250", PATHS  # collection URLs read just that collection
check.check_site({"name": "a", "url": f"http://127.0.0.1:{SHOPIFY_PORT}/collections/all"}, CFG, None)
assert PATHS[-1] == "/products.json?limit=250", PATHS  # except /collections/all, read store-wide
st = json.loads(json.dumps(st))  # round-trip like state.json

# restock + new over-RRP listing alert; filtered product does not
SHOP["products"] = [product(1, "Delta Reign Elite Trainer Box", "49.99", True), product(3, "Booster Bundle X", "24.99", True),
                    product(4, "Some Elite Trainer Box", "89.99", True), product(2, "Pokemon Sleeves", "5", True)]
st, alerts = check.check_site(site, CFG, st)
texts = [t for t, _ in alerts]
assert alerts[0][1] == f"http://127.0.0.1:{SHOPIFY_PORT}/cart/1:1", alerts  # alerts open the basket
assert texts == ["BACK IN STOCK: Delta Reign Elite Trainer Box £49.99", "NEW: Booster Bundle X £24.99",
                 "NEW: Some Elite Trainer Box £89.99 (over RRP)"], texts
st, alerts = check.check_site(site, CFG, json.loads(json.dumps(st)))
assert alerts == [], alerts  # nothing changed, no repeat alerts

# Shopify rate limit (429) keeps the last state instead of falling back to page mode
import urllib.error
real = check.fetch
check.fetch = lambda u: (_ for _ in ()).throw(urllib.error.HTTPError(u, 429, "Too Many Requests", {}, None))
try:
    check.check_site(site, CFG, st); raise AssertionError("429 should raise")
except urllib.error.HTTPError:
    pass
check.fetch = real

# Non-Shopify page: diff of matching lines
site = {"name": "p", "url": f"http://127.0.0.1:{PAGE_PORT}/pokemon"}
CFG["sets"] = ["delta reign"]
PAGE["html"] = "<html><script>elite trainer</script><a>Pokemon Old Elite Trainer Box</a><p>Sleeves</p><a>Lorcana Booster Bundle</a></html>"
st, alerts = check.check_site(site, CFG, None)
assert st == {"mode": "page", "lines": ["Pokemon Old Elite Trainer Box"]} and alerts == [], st  # non-Pokemon lines hidden
PAGE["html"] += '<a>Delta Reign Booster Bundle</a><script>x={\\"name\\":\\"ME Delta Reign Elite Trainer Box\\"}</script>'
st, alerts = check.check_site(site, CFG, st)
# product names inside page JSON (escaped, as BigCommerce embeds them) count too
assert [t for t, _ in alerts] == ["New on page: Delta Reign Booster Bundle", "New on page: ME Delta Reign Elite Trainer Box"], alerts

# Dashboard rows: Shopify items keep stock/price; page lines are listed
rows = check.site_status({"name": "p", "url": "u"}, st, None, CFG)
assert [i["title"] for i in rows["items"]] == ["Delta Reign Booster Bundle", "ME Delta Reign Elite Trainer Box", "Pokemon Old Elite Trainer Box"], rows
shop = check.site_status({"name": "s", "url": "u"}, {"mode": "shopify", "items": {"4": ["Some Elite Trainer Box", 89.99, True, "x"]}}, None, CFG)
shop = check.site_status({"name": "s", "url": "u"}, {"mode": "shopify", "items": {"4": ["Some Elite Trainer Box", 89.99, True, "https://shop.uk/products/etb"]}}, None, CFG)
assert shop["items"] == [{"title": "Some Elite Trainer Box", "price": 89.99, "available": True, "url": "https://shop.uk/products/etb",
                          "over_rrp": True, "rrp": 49.99, "cart": "https://shop.uk/cart/4:1"}], shop
check.send_email([])  # no SMTP env: must be a silent no-op

# Wishlist: all terms must match, price must be within max (page lines have no price)
W = {"wishlist": [{"name": "DR ETB", "match": ["delta reign", "elite trainer"], "max": 50}]}
assert check.wish_hit("NEW: Delta Reign Elite Trainer Box £49.99", W) == "DR ETB"
assert check.wish_hit("NEW: Delta Reign Elite Trainer Box £89.99 (over RRP)", W) is None
assert check.wish_hit("Spotted: Delta Reign Elite Trainer Box", W) == "DR ETB"
assert check.wish_hit("NEW: Delta Reign Booster Bundle £24.99", W) is None

# Drop reminders: once, only inside the 6h window
from datetime import datetime, timezone
now = datetime(2026, 11, 5, 9, tzinfo=timezone.utc)
drops = [{"shop": "Chaos Cards", "name": "Ballot", "url": "u", "closes": "2026-11-05T12:00:00+00:00"},
         {"shop": "X", "name": "Later", "url": "u", "closes": "2026-11-06T12:00:00+00:00"},
         {"shop": "Y", "name": "No date", "url": "u", "closes": None}]
reminded = []
assert check.drop_reminders(drops, now, reminded) == [("Chaos Cards", "Closes in 3h: Ballot", "u")]
assert check.drop_reminders(drops, now, reminded) == []

# Drop pages: first sight lists every product (shown, not alerted), then only new ones; menu links ignored
dp = {"shop": "Chaos Cards", "url": f"http://127.0.0.1:{PAGE_PORT}/calendar", "type": "ballot"}
PAGE["html"] = '<a href="/prod/pokemon-old">Old Elite Trainer Box</a><a href="/shop/etbs">Elite Trainer Boxes</a><a href="/basket">Basket</a>'
st, new = check.check_drop_page(dp, CFG, None)
assert [(d["name"], d["seeded"]) for d in new] == [("Old Elite Trainer Box", True)] and st["links"] == [f"http://127.0.0.1:{PAGE_PORT}/prod/pokemon-old"], (st, new)
PAGE["html"] += '<a href="/prod/pokemon-dr-bundle#x"><span>Delta Reign Booster Bundle</span></a>'
st, new = check.check_drop_page(dp, CFG, st)
assert [(d["name"], d["url"], d["type"], d["seeded"]) for d in new] == [("Delta Reign Booster Bundle", f"http://127.0.0.1:{PAGE_PORT}/prod/pokemon-dr-bundle", "ballot", False)], new
st, new = check.check_drop_page(dp, CFG, st)
assert new == []

# Release window: day before and day of
from datetime import date
R = [{"date": "2026-11-06"}]
assert [check.release_window(R, date(2026, 11, d)) for d in (4, 5, 6, 7)] == [False, True, True, False]
print("all checks passed")

# RRP: longest matching key wins; got items stop alerting
R = {"rrp": {"elite trainer": 49.99, "pokemon center elite trainer": 56.99}}
assert check.rrp_for("Pokemon Center Elite Trainer Box", R) == 56.99 and check.rrp_for("Some Elite Trainer Box", R) == 49.99
assert check.rrp_for("Mini Tin", R) is None and not check.over_rrp("Pokemon Center Elite Trainer Box", 56.99, R)
G = {"wishlist": [{"name": "DR ETB", "match": ["delta reign", "elite trainer"], "max": 50, "got": {"shop": "Zatu Games", "date": "2026-11-06"}}]}
assert check.wish_hit("NEW: Delta Reign Elite Trainer Box £49.99", G) is None
assert check.got_it("New pre-order spotted: Delta Reign Elite Trainer Box", G)
assert not check.got_it("NEW: Delta Reign Booster Bundle £24.99", G)
print("rrp/got checks passed")


# notify reports which channels worked; none configured means none
assert check.notify("s", "t", "u") == []
print("notify checks passed")

# Ballots/pre-orders spotted in any shop's listings; over-RRP pre-orders skipped
LD = {"rrp": {"elite trainer": 49.99}}
st = {"items": {"1": ["Pokemon Delta Reign Elite Trainer Box (PRE-ORDER)", 49.99, True, "https://s/products/a"],
                "2": ["Pokemon Delta Reign Elite Trainer Box Pre Order", 89.99, True, "https://s/products/b"],
                "3": ["Pokemon 30th UPC - Ballot Entry", 169.99, True, "https://s/products/c"]}}
got = check.listing_drops("S", st, LD)
assert sorted((d["name"], d["type"]) for d in got.values()) == [("Pokemon 30th UPC - Ballot Entry", "ballot"), ("Pokemon Delta Reign Elite Trainer Box (PRE-ORDER)", "pre-order")], got
assert list(check.listing_drops("P", {"lines": ["Pokemon Mew Figure Collection - Product Drop"]}, LD).values())[0]["url"] is None
tagged = {"products": [{**product(7, "Pokemon Chaos Rising Elite Trainer Box", 49.99, True), "tags": ["Pre-Order", "Pokemon"]}]}
assert list(check.shopify_items(tagged, "b", LD | {"include": ["elite trainer"], "exclude": []}).values())[0][0] == "Pokemon Chaos Rising Elite Trainer Box (Pre-Order)"
print("listing drop checks passed")

# Expected dates from product pages
assert check.expected_date("<b>Available for Pre-Order soon.</b> Expected 06/11/2026") == "2026-11-06"
assert check.expected_date("<p>Release date: 6th November 2026</p>") == "2026-11-06"
assert check.expected_date("Releases on 22 May 2026, ships fast") == "2026-05-22"
assert check.expected_date("Pokemon ETB 49.99") is None
print("date checks passed")
