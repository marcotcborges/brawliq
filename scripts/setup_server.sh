#!/bin/bash
# Run once as root on a fresh Hetzner Ubuntu 22.04 server.
# Usage: bash setup_server.sh
set -euo pipefail

# ── config (edit before running) ──────────────────────────────────────────────
REPO_URL="https://github.com/marcotcborges/brawliq.git"
APP_USER="brawliq"
APP_DIR="/home/$APP_USER/app"
DATA_DIR="/home/$APP_USER/data"
# ──────────────────────────────────────────────────────────────────────────────

echo "==> [1/9] Updating system packages"
apt-get update -q && apt-get upgrade -y -q

echo "==> [2/9] Installing dependencies"
apt-get install -y -q git nginx python3.11 python3.11-venv python3-pip ufw

echo "==> [3/9] Configuring firewall"
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo "==> [4/9] Creating app user and directories"
id "$APP_USER" &>/dev/null || useradd -m -s /bin/bash "$APP_USER"
mkdir -p "$DATA_DIR"
chown "$APP_USER:$APP_USER" "$DATA_DIR"

echo "==> [5/9] Cloning repo and installing Python dependencies"
sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
sudo -u "$APP_USER" bash -c "
  cd $APP_DIR
  python3.11 -m venv .venv
  .venv/bin/pip install -q -e .
"

echo "==> [6/9] Creating .env — add your API key before starting the service"
sudo -u "$APP_USER" tee "$APP_DIR/.env" > /dev/null << EOF
BRAWLSTARS_API_KEY=REPLACE_WITH_YOUR_KEY
DATA_DIR=$DATA_DIR
EOF
chmod 600 "$APP_DIR/.env"

echo "==> [7/9] Creating systemd service"
tee /etc/systemd/system/brawliq.service > /dev/null << EOF
[Unit]
Description=BrawlIQ Streamlit App
After=network.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/streamlit run app.py \\
    --server.port=8501 \\
    --server.address=127.0.0.1 \\
    --server.headless=true
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable brawliq

echo "==> [8/9] Configuring nginx reverse proxy"
tee /etc/nginx/sites-available/brawliq > /dev/null << 'EOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass         http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade    $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host       $host;
        proxy_cache_bypass $http_upgrade;
    }
}
EOF

ln -sf /etc/nginx/sites-available/brawliq /etc/nginx/sites-enabled/brawliq
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl enable nginx && systemctl restart nginx

echo "==> [9/9] Setting up cron job (every 30 minutes)"
(sudo -u "$APP_USER" crontab -l 2>/dev/null; echo "*/30 * * * * cd $APP_DIR && $APP_DIR/.venv/bin/python -m scripts.fetch_player_data >> $DATA_DIR/cron.log 2>&1") \
  | sudo -u "$APP_USER" crontab -

echo ""
echo "======================================================"
echo " Setup complete. Two steps left:"
echo ""
echo " 1. Add your API key:"
echo "    nano $APP_DIR/.env"
echo ""
echo " 2. Start the app:"
echo "    systemctl start brawliq"
echo ""
echo " Then visit http://$(curl -s ifconfig.me)"
echo "======================================================"
