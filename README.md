# Pokemon TCG UK stock alerts

Checks UK shops about every 15 minutes (every 5 on release days), shows everything on a live dashboard
(`index.html`, refreshes itself every minute), and emails you plus pings your phone when a
Pokemon product you care about is **newly listed** or **back in stock**. It
shows the price and flags anything **over RRP**. It never buys anything.

## Set it up (about 10 minutes, free)

1. **Phone alerts:** install the free **ntfy** app (iOS/Android) and subscribe
   to a hard-to-guess topic name, e.g. `kyle-poke-7f3k2`. (Or use a Discord
   channel webhook instead, or as well.)
2. **GitHub:** create a new repository and upload these files, keeping
   `.github/workflows/check.yml` in place. A **public** repo gets unlimited
   free Actions minutes (your secrets stay hidden), so use public. The
   workflow wakes every 5 minutes: on release days (the day before and the
   day of) it scans every time, otherwise every 15 minutes. A private repo's
   2,000 free minutes won't cover that.
3. In the repo go to **Settings > Secrets and variables > Actions** and add
   `DISCORD_WEBHOOK` if you want Discord alerts. Phone push uses the public ntfy
   topic in `sites.json` (`ntfy_topic`); anyone can subscribe from the dashboard's
   Notifications tab. Every notification sent is logged in `notifications.json`.
   For **email** (one digest email per run that finds restocks): with Gmail,
   turn on 2-step verification, create an **App password** at
   myaccount.google.com/apppasswords, then add secrets `SMTP_USER` (your
   Gmail address) and `SMTP_PASS` (the app password). Optional: `EMAIL_TO`
   to send to a different address, `SMTP_HOST` for a non-Gmail provider
   (port 587, STARTTLS). Outlook/Hotmail no longer allows password login
   for scripts, so send from Gmail and set `EMAIL_TO` to any inbox you like.
4. **Dashboard:** in the repo go to **Settings > Pages**, pick *Deploy from a
   branch*, branch `main`, folder `/ (root)`. Your dashboard will be at
   `https://<your-username>.github.io/<repo-name>/`. Bookmark it on your phone.
5. Go to **Actions > Pokemon stock check > Run workflow** once. The first run
   only records what's on each site, so you won't get alerts until something changes.

To run it on your own computer instead: `NTFY_TOPIC=your-topic python3 check.py`
(Python 3, nothing to install). Run `python3 test_check.py` to self-test.

## Tuning: `sites.json`

- `include` / `exclude`: which product titles count (lowercase phrases).
- `rrp`: price above which an alert gets the "(over RRP)" tag.
- `sites`: add or remove shops. Point `url` at the shop's Pokemon or
  pre-orders page. Shopify shops are detected automatically and give
  exact stock and price data; other shops get their page compared for new
  product names.
- Sites with `skip` (Pokemon Center, Smyths, Argos, GAME, Amazon) are
  listed but **not checked**. Their terms forbid scraping, and they block
  bots anyway. Cover them with a free alert Discord such as Oak Labs.

If a site fails, the Actions log shows `check failed`. Usually the URL
has moved, so update it in `sites.json`.

## Upcoming releases: `releases.json`

The dashboard's **Coming soon** and **Pre-order pages** sections read this file.
Add a release with its date (`YYYY-MM-DD`), `confirmed` or `rumoured`, and
its products. Each release drops off 14 days after its date. Edit `shops` to
change the shop links.

## Wishlist and drops

- **Wishlist** (`wishlist.json`): tap **+ want** on any upcoming product and
  it's added at its UK RRP, with no prices to type. Items you add in
  **Edit wishlist** get the RRP for their product type (from the `rrp` table
  in `releases.json`). The first save asks for a fine-grained GitHub token
  (this repo only, Contents: read and write), stored only in that browser.
  **Only wishlist items at or under RRP send email or push alerts.** Items
  with no announced RRP alert at any price until one is added.
- `drops` in `releases.json`: open or upcoming ballots and pre-order windows.
  `closes` is a time like `2026-11-05T12:00:00+00:00`, or `null` if it's not
  known yet. You get one email/push reminder when a drop is within 6 hours of
  closing. The "I've entered" ticks are saved in your browser.

## Automatic drop detection and readiness

- `drop_pages` in `sites.json`: pages watched for new drops (the Chaos Cards
  release calendar and Double Sleeved pre-orders). A new Pokemon product
  link there lands in the dashboard's Drops panel and always sends an alert,
  even when it's not on your wishlist. They stay for 30 days.
- **Ready for drops** on the dashboard: tick off account, card saved,
  newsletter and notify-me for each shop. Ticks are saved on that device only.

## Release day, baskets, resale, patterns, orders

- **Release-day mode** switches on automatically the day before and the day
  of a release in `releases.json`. You get a big countdown, one-tap links to
  your best wishlist prices and the shops, and the page refreshes every 20
  seconds.
- **⚡ Basket** (Shopify shops only) opens the shop with that item already in
  your basket. You still check out yourself; nothing is bought automatically.
- **Resale:** each wishlist card links to eBay UK sold listings.
- **Restock patterns:** the checker logs every restock to `history.json`.
  The dashboard shows a day-by-hour heatmap in UK time, for all shops or one.
- **My orders:** log pre-orders and purchases. It warns about items ordered
  twice and cancel windows ending within 3 days, and totals what's committed
  and spent. Orders save to `orders.json` in your repo when the GitHub key
  is set (otherwise only on that device).

## Faster buying

- **Alerts open the basket:** for Shopify shops, tapping an email or push
  alert opens the shop with the item already in your basket.
- **Add to Home Screen:** open the dashboard in Safari (iPhone: Share → Add to
  Home Screen) or Chrome (Android: ⋮ → Add to Home screen / Install app). It
  then opens full screen like an app.
- **Amazon invites tab:** every Amazon UK invite-only Pokemon item I could
  find (`amazon` in `releases.json`). Tap **Request invite →**, then **Mark
  opted in**. You can add your own amazon.co.uk links. Opt-ins save to
  `amazon.json` (with your GitHub key) or to the device. Amazon isn't scanned
  automatically, because its terms forbid it.
