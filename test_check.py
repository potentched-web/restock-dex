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

# Non-Shopify page: diff of matching lines
site = {"name": "p", "url": f"http://127.0.0.1:{PAGE_PORT}/pokemon"}
PAGE["html"] = "<html><script>elite trainer</script><a>Old Elite Trainer Box</a><p>Sleeves</p></html>"
st, alerts = check.check_site(site, CFG, None)
assert st == {"mode": "page", "lines": ["Old Elite Trainer Box"]} and alerts == [], st
PAGE["html"] += '<a>Delta Reign Booster Bundle</a><script>x={\\"name\\":\\"ME Delta Reign Elite Trainer Box\\"}</script>'
st, alerts = check.check_site(site, CFG, st)
# product names inside page JSON (escaped, as BigCommerce embeds them) count too
assert [t for t, _ in alerts] == ["New on page: Delta Reign Booster Bundle", "New on page: ME Delta Reign Elite Trainer Box"], alerts

# Dashboard rows: Shopify items keep stock/price; page lines are listed
rows = check.site_status({"name": "p", "url": "u"}, st, None, CFG)
assert [i["title"] for i in rows["items"]] == ["Delta Reign Booster Bundle", "ME Delta Reign Elite Trainer Box", "Old Elite Trainer Box"], rows
shop = check.site_status({"name": "s", "url": "u"}, {"mode": "shopify", "items": {"4": ["Some Elite Trainer Box", 89.99, True, "x"]}}, None, CFG)
shop = check.site_status({"name": "s", "url": "u"}, {"mode": "shopify", "items": {"4": ["Some Elite Trainer Box", 89.99, True, "https://shop.uk/products/etb"]}}, None, CFG)
assert shop["items"] == [{"title": "Some Elite Trainer Box", "price": 89.99, "available": True, "url": "https://shop.uk/products/etb",
                          "over_rrp": True, "cart": "https://shop.uk/cart/4:1"}], shop
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

# Drop pages: seed silently, then report only newly linked products (absolute URLs)
dp = {"shop": "Chaos Cards", "url": f"http://127.0.0.1:{PAGE_PORT}/calendar", "type": "ballot"}
PAGE["html"] = '<a href="/prod/old">Old Elite Trainer Box</a><a href="/basket">Basket</a>'
st, new = check.check_drop_page(dp, CFG, None)
assert new == [] and st["links"] == [f"http://127.0.0.1:{PAGE_PORT}/prod/old"], (st, new)
PAGE["html"] += '<a href="/prod/dr-bundle#x"><span>Delta Reign Booster Bundle</span></a>'
st, new = check.check_drop_page(dp, CFG, st)
assert [(d["name"], d["url"], d["type"]) for d in new] == [("Delta Reign Booster Bundle", f"http://127.0.0.1:{PAGE_PORT}/prod/dr-bundle", "ballot")], new
st, new = check.check_drop_page(dp, CFG, st)
assert new == []

# Release window: day before and day of
from datetime import date
R = [{"date": "2026-11-06"}]
assert [check.release_window(R, date(2026, 11, d)) for d in (4, 5, 6, 7)] == [False, True, True, False]
print("all checks passed")
