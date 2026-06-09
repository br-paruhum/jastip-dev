# Deployment

Two targets on `43.129.52.60` (user `claude`):

- **Staging:** `/var/www/jastip-stg/`
- **Production:** `/var/www/jastip-prd/`

## 1. Server prerequisites (once)

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip postgresql nginx
# Node 18+ for the WhatsApp bot
```

## 2. Database (Postgres)

```bash
sudo -u postgres psql -c "CREATE ROLE jastip LOGIN PASSWORD 'STRONG_PASSWORD';"
sudo -u postgres createdb -O jastip jastip_stg
```

## 3. App

```bash
cd /var/www/jastip-stg
git clone https://github.com/br-paruhum/jastip-dev.git .
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env:
#   DEBUG=False
#   SECRET_KEY=<generate>
#   ALLOWED_HOSTS=stg.jastip.me
#   CSRF_TRUSTED_ORIGINS=https://stg.jastip.me
#   SITE_DOMAIN=stg.jastip.me
#   DATABASE_URL=postgres://jastip:STRONG_PASSWORD@127.0.0.1:5432/jastip_stg
#   EMAIL_USE_SMTP=True  (+ EMAIL_HOST/USER/PASSWORD for admin@jastip.me)
#   GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_SECRET
#   ADMIN_PASSWORD=<strong>

python manage.py migrate
python manage.py seed
python manage.py collectstatic --noinput
```

## 4. Gunicorn (systemd)

`/etc/systemd/system/jastip-stg.service`:

```ini
[Unit]
Description=Jastip staging (gunicorn)
After=network.target

[Service]
User=claude
WorkingDirectory=/var/www/jastip-stg
EnvironmentFile=/var/www/jastip-stg/.env
ExecStart=/var/www/jastip-stg/.venv/bin/gunicorn config.wsgi:application \
  --bind 127.0.0.1:8019 --workers 3
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now jastip-stg
```

## 5. Nginx

```nginx
server {
    listen 80;
    server_name stg.jastip.me;

    location /static/ { alias /var/www/jastip-stg/staticfiles/; }
    location /media/  { alias /var/www/jastip-stg/media/; }

    location / {
        proxy_pass http://127.0.0.1:8019;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    client_max_body_size 5M;   # item/payment photo uploads
}
```

Then `sudo certbot --nginx -d stg.jastip.me` for HTTPS.

## 6. WhatsApp bot (optional, separate service)

```bash
cd /var/www/jastip-stg/whatsapp-bot
npm install
cp .env.example .env   # set WHATSAPP_BOT_TOKEN to match Django
npm start              # scan the QR once; run under pm2/systemd for persistence
```

## Production

Same steps with `jastip-prd`, `jastip_prd` DB, `jastip.me` host, and a separate
systemd unit on its own port.
