"""Resale margins: UK RRP vs recent Cardmarket sale prices for sealed Pokemon TCG. Stdlib only.

Reads Cardmarket's public daily downloads (sealed product list + price guide, EUR),
converts to GBP with the ECB rate, and writes prices.json for the dashboard's Resale tab.
RRP comes from sites.json "rrp" (same rules as the stock checker). Run once a day.
"""
import json, os
from datetime import datetime, timezone
from urllib.parse import quote_plus
from check import HERE, fetch, rrp_for, wanted

CM = "https://downloads.s3.cardmarket.com/productCatalog"
PRODUCTS = CM + "/productList/products_nonsingles_6.json"  # 6 = Pokemon
GUIDE = CM + "/priceGuide/price_guide_6.json"
FX = "https://api.frankfurter.app/latest?from=EUR&to=GBP"
TARGET = 0.5  # flag anything selling for 50%+ over RRP


def rows(doc):
    """Cardmarket files wrap their list in a top-level object; take the first list in it."""
    return doc if isinstance(doc, list) else next(v for v in doc.values() if isinstance(v, list))


def sold_eur(g):
    """Recent sold price: 7-day average of sales, falling back to the trend and 30-day average."""
    for k in ("avg7", "trend", "avg30", "avg"):
        if g.get(k):
            return float(g[k])
    return None


def build(products, guide, eur_gbp, cfg, sets):
    prices = {g["idProduct"]: g for g in guide}
    out = []
    for p in products:
        name, g = p.get("name", ""), prices.get(p.get("idProduct"))
        rrp = rrp_for(name, cfg) if wanted(name, cfg) else None
        eur = sold_eur(g) if g else None
        if not rrp or not eur:
            continue
        sold = round(eur * eur_gbp, 2)
        out.append({
            "name": name, "rrp": rrp, "sold": sold,
            "low": round(float(g["low"]) * eur_gbp, 2) if g.get("low") else None,
            "avg30": round(float(g["avg30"]) * eur_gbp, 2) if g.get("avg30") else None,
            "margin": round((sold - rrp) / rrp, 3), "profit": round(sold - rrp, 2),
            "tracked": any(s.lower() in name.lower() for s in sets),
            "cardmarket": "https://www.cardmarket.com/en/Pokemon/Products/Search?searchString=" + quote_plus(name),
            "ebay_sold": "https://www.ebay.co.uk/sch/i.html?LH_Sold=1&LH_Complete=1&_nkw=" + quote_plus("pokemon " + name),
        })
    return sorted(out, key=lambda i: -i["margin"])


def main():
    cfg = json.load(open(os.path.join(HERE, "sites.json")))
    sets = [r["set"] for r in json.load(open(os.path.join(HERE, "releases.json")))["releases"] if r.get("set")]
    eur_gbp = json.loads(fetch(FX))["rates"]["GBP"]
    items = build(rows(json.loads(fetch(PRODUCTS))), rows(json.loads(fetch(GUIDE))), eur_gbp, cfg, sets)
    json.dump({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "eur_gbp": eur_gbp,
               "target": TARGET, "source": "Cardmarket price guide (EUR, converted to GBP)", "items": items},
              open(os.path.join(HERE, "prices.json"), "w"), indent=1)
    print(f"{len(items)} priced, {sum(i['margin'] >= TARGET for i in items)} at {TARGET:.0%}+ over RRP")


if __name__ == "__main__":
    main()
