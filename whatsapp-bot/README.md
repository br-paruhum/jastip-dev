# Jastip WhatsApp bot (Baileys)

A small Node service that sends WhatsApp messages on behalf of Jastip.me. Django
talks to it over HTTP (`apps/notifications/whatsapp.py` → `BaileysProvider`).

> ⚠️ Baileys is an **unofficial** WhatsApp library. Use a dedicated number you're
> willing to risk. For production scale, swap in the official WhatsApp Cloud API
> (the Django side is pluggable — only `whatsapp.py` changes).

## Run

```bash
cd whatsapp-bot
cp .env.example .env        # set WHATSAPP_BOT_TOKEN to match Django's
npm install
npm start
```

On first start a **QR code** prints in the terminal. Open WhatsApp on the admin
phone → *Linked devices* → *Link a device* → scan it. Credentials persist in
`auth_info/` (gitignored), so restarts stay logged in.

## API

| Method | Path       | Auth            | Body                          |
|--------|------------|-----------------|-------------------------------|
| GET    | `/healthz` | none            | —                             |
| GET    | `/status`  | `Bearer <token>`| —                             |
| POST   | `/send`    | `Bearer <token>`| `{ "to": "+62...", "message": "..." }` |

## Enable in Django

In the Django `.env`:

```
WHATSAPP_ENABLED=True
WHATSAPP_BOT_URL=http://127.0.0.1:8090
WHATSAPP_BOT_TOKEN=<same token as here>
```

When `WHATSAPP_ENABLED=False`, Django logs messages instead of sending them, so
the rest of the app works without the bot running.
