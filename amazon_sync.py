"""Weekly: re-reads TCG Compare's Amazon UK invite-only list and adds new product pages to releases.json "amazon".
Never fetches Amazon itself. Items TCG Compare no longer lists are kept (your opt-ins stay) and named in the ping.
Run by .github/workflows/amazon.yml."""
import html as H, json, os, re, sys
from check import fetch, notify, HERE

SOURCE = "https://www.tcgcompare.com/amazon"
RELEASES = os.path.join(HERE, "releases.json")
BASE_SET = re.compile(r"^(Booster Bundle|Half Booster Box|Elite Trainer Box|\w+ 3-Pack Blister|Mega \w+ Elite Trainer Box)$")


def short(name):
    """'Pokemon - Mega Evolution - Pitch Black - Half Booster Box' -> 'Pitch Black Half Booster Box'."""
    n = re.sub(r"^Pok[eé]mon( TCG)?\s*-\s*", "", name)
    era = re.match(r"^(Mega Evolution|Scarlet & Violet|Sword & Shield)\s*-\s*", n)
    n = re.sub(r"\s+", " ", n[era.end():] if era else n).replace(" - ", " ").strip()
    return f"Mega Evolution {n}" if era and era.group(1) == "Mega Evolution" and BASE_SET.match(n) else n


def invite_products(page):
    """[(asin, name)] for every UK invite-only Pokémon card on the page."""
    out = []
    for card in re.split(r'<div id="(?=[A-Z0-9]{10}" class="group relative")', page)[1:]:
        asin, name = card[:10], re.search(r"<h3[^>]*>(.*?)</h3>", card, re.S)
        if name and "Invite Only" in card and "Pok" in card and f"amazon.co.uk/dp/{asin}" in card:
            out.append((asin, H.unescape(name.group(1)).strip()))
    return out


def sync(page, amazon):
    """Returns (new entries to append, names of listed items TCG Compare no longer shows)."""
    found = invite_products(page)
    if not found:
        raise ValueError("no invite products found: TCG Compare's page layout probably changed")
    have = {m.group(1) for a in amazon if (m := re.search(r"/dp/([A-Z0-9]{10})", a["url"]))}
    seen = {a for a, _ in found}
    new = [{"name": short(n), "url": f"https://www.amazon.co.uk/dp/{a}", "kind": "product"} for a, n in found if a not in have]
    gone = [a["name"] for a in amazon if (m := re.search(r"/dp/([A-Z0-9]{10})", a["url"])) and m.group(1) not in seen]
    return new, gone


def main():
    rel = json.load(open(RELEASES, encoding="utf-8"))
    new, gone = sync(fetch(SOURCE), rel.get("amazon", []))
    print(f"{len(new)} new, {len(gone)} no longer listed on TCG Compare: {gone}")
    if new:
        rel["amazon"] = rel.get("amazon", []) + new
        open(RELEASES, "w", encoding="utf-8").write(json.dumps(rel, indent=2, ensure_ascii=False) + "\n")
        cfg = json.load(open(os.path.join(HERE, "sites.json"), encoding="utf-8"))
        topics = list(dict.fromkeys(t for t in (cfg.get("ntfy_topic"), os.environ.get("NTFY_TOPIC")) if t))
        notify("Amazon invites", f"{len(new)} new invite product(s): " + ", ".join(e["name"] for e in new)[:900],
               "https://potentched-web.github.io/restock-dex/#amazon", topics)


if __name__ == "__main__":
    sys.exit(main())
