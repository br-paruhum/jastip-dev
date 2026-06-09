# Jastip.me

Proxy purchasing platform. Travelers with spare luggage space carry items for
buyers who want goods from abroad; Jastip.me holds funds in escrow for a 2.5% fee.

- **Stack:** Django 5 · PostgreSQL · django-allauth (Google + email) · django-unfold admin · WhiteNoise
- **Design:** Montserrat, Anthropic-inspired clay/ivory palette, responsive, SEO + AdSense ready
- **Notifications:** HTML email (admin cc'd) + WhatsApp via a Baileys microservice

## The 8-step lifecycle

`New → Request Received → Accepted/Reopen → Deposit Paid → Item(s) Purchased →
Package Arrived → Ready for Pickup → Closed`

## Quick start (local)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # generate a SECRET_KEY, fill values

# Database: SQLite works out of the box. For Postgres (recommended):
bash scripts/setup_db.sh        # needs sudo; creates jastip_dev DB + role
#   then set DATABASE_URL in .env

python manage.py migrate
python manage.py seed           # site pages, FAQ, admin user, demo data
python manage.py runserver 8019
```

Visit http://127.0.0.1:8019 · admin at `/admin/` (`admin@jastip.me`, password
from `ADMIN_PASSWORD` env, default `ChangeMe!2026`).

### Google OAuth
Put `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_SECRET` in `.env`, then re-run
`python manage.py seed` to register the social app. Authorized redirect URI:
`http://localhost:8019/accounts/google/login/callback/`.

### WhatsApp
See [`whatsapp-bot/README.md`](whatsapp-bot/README.md). Leave `WHATSAPP_ENABLED=False`
to develop without it (messages are logged, not sent).

## Project layout

```
config/            settings, urls, Unfold theme
apps/accounts/     custom email User, profile + WhatsApp OTP, allauth adapter
apps/trips/        TravelPlan, BuyRequest, RequestItem, Transaction, Payment, workflow
apps/blog/         Post
apps/pages/        SitePage, FAQ, home, sitemap, robots
apps/notifications/ email + WhatsApp services, NotificationLog
whatsapp-bot/      Node Baileys microservice
templates/ static/ front-end
```

## Tests

```bash
python manage.py test
```

## Deploy

See [`DEPLOY.md`](DEPLOY.md) for staging (`/var/www/jastip-stg`) and production.
