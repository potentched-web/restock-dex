"""Temporary: find Pokemon listing URLs for candidate shops. Prints a report."""
import json, re, sys, time
import check
cfg = json.load(open("sites.json"))
cfg["sets"] = []
shops = sys.argv[1:]
for base in shops:
    base = base.rstrip("/")
    out = []
    try:
        html = check.fetch(base + "/")
        links = sorted({h for h in re.findall(r'href="([^"#]+)"', html) if re.search(r"pok", h, re.I)})[:25]
        out.append("links: " + " ".join(links))
    except Exception as e:
        out.append(f"home err {e}"); links = []
    cols = [l.split("?")[0] for l in links if "/collections/" in l and "/products/" not in l][:6]
    for c in cols:
        time.sleep(1)
        path = c if c.startswith("/") else re.sub(r"^https?://[^/]+", "", c)
        try:
            d = json.loads(check.fetch(base + path + "/products.json?limit=250"))
            n = len(check.shopify_items(d, base, cfg))
            out.append(f"  {path}: products={len(d['products'])} wanted={n}")
        except Exception as e:
            out.append(f"  {path}: err {str(e)[:60]}")
    print("==", base); print("\n".join(out), flush=True)
    time.sleep(1)
