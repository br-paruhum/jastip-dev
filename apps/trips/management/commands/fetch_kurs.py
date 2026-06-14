"""Fetch BCA foreign-exchange rates and upsert into ExchangeRate.

Intended to run daily at 09:00 WIB (server tz = Asia/Jakarta).

    python manage.py fetch_kurs            # normal run
    python manage.py fetch_kurs --dry-run  # print parsed rates, no DB write

Flow:
  1. GET the BCA kurs page to obtain a session cookie + the Sitecore dsid.
  2. POST to RefreshKurs with that dsid to receive JSON rates.
  3. Extract TT Counter (RateType=1) SellRate + BuyRate per currency.
  4. Upsert into ExchangeRate (create or update; never delete so is_active
     choices made in admin are preserved).
"""

import re
from datetime import datetime, timezone as dt_timezone

import requests
from django.core.management.base import BaseCommand

from apps.trips.models import ExchangeRate

KURS_PAGE = "https://www.bca.co.id/id-ID/informasi/kurs"
KURS_API = "https://www.bca.co.id/api/sitecore/currencies/RefreshKurs"
DSID_RE = re.compile(r'dsid="(\{[^"]+\})"')

HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

TT_COUNTER = 1  # RateType index for TT Counter in BCA's API


def _parse_bca_date(value: str) -> datetime | None:
    """Convert BCA's /Date(ms)/ format to an aware datetime."""
    m = re.search(r"Date\((\d+)\)", value or "")
    if not m:
        return None
    return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=dt_timezone.utc)


class Command(BaseCommand):
    help = "Fetch BCA TT Counter exchange rates and upsert into ExchangeRate."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Parse and print rates without writing to the database.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        session = requests.Session()
        session.headers.update(HEADERS_BASE)

        # Step 1 — GET the kurs page: sets session cookies and gives us dsid.
        self.stdout.write("Fetching BCA kurs page…")
        try:
            page_resp = session.get(KURS_PAGE, timeout=20)
            page_resp.raise_for_status()
        except requests.RequestException as exc:
            self.stderr.write(self.style.ERROR(f"Failed to load kurs page: {exc}"))
            raise SystemExit(1)

        m = DSID_RE.search(page_resp.text)
        if not m:
            self.stderr.write(self.style.ERROR("Could not find dsid in BCA kurs page HTML."))
            raise SystemExit(1)
        dsid = m.group(1)
        self.stdout.write(f"dsid: {dsid}")

        # Step 2 — POST RefreshKurs.
        try:
            api_resp = session.post(
                KURS_API,
                data={"dsid": dsid},
                headers={
                    "Referer": KURS_PAGE,
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": "https://www.bca.co.id",
                },
                timeout=20,
            )
            api_resp.raise_for_status()
            data = api_resp.json()
        except requests.RequestException as exc:
            self.stderr.write(self.style.ERROR(f"RefreshKurs call failed: {exc}"))
            raise SystemExit(1)

        if data.get("Message") != "Success":
            self.stderr.write(self.style.ERROR(f"API returned non-success: {data.get('Message')}"))
            raise SystemExit(1)

        # Timestamp from the TT Counter block (RateType=1).
        tt_meta = next((r for r in data.get("RateTypes", []) if r["RateType"] == TT_COUNTER), None)
        tt_ts = _parse_bca_date(tt_meta["LastUpdate"]) if tt_meta else None

        # Step 3 — Parse and upsert.
        created = updated = skipped = 0
        for item in data.get("KursRates", []):
            code = item["CurrencyCode"]
            name = item.get("CurrencyName") or code

            tt = next((r for r in item.get("Rates", []) if r["RateType"] == TT_COUNTER), None)
            if tt is None:
                self.stdout.write(f"  skip {code}: no TT Counter rate")
                skipped += 1
                continue

            sell = tt["SellRate"]
            buy = tt["BuyRate"]

            if dry:
                self.stdout.write(f"  [dry] {code:6s} {name:6s}  sell={sell:>12,.2f}  buy={buy:>12,.2f}")
                continue

            obj, is_new = ExchangeRate.objects.get_or_create(
                code=code,
                defaults={"name": name, "sell_rate": sell, "buy_rate": buy, "updated_at": tt_ts},
            )
            if not is_new:
                obj.name = name
                obj.sell_rate = sell
                obj.buy_rate = buy
                obj.updated_at = tt_ts
                obj.save(update_fields=["name", "sell_rate", "buy_rate", "updated_at"])
                updated += 1
            else:
                created += 1

        if dry:
            self.stdout.write(self.style.WARNING("Dry run — nothing written."))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Done. Created: {created}, updated: {updated}, skipped (no TT rate): {skipped}."
            ))
