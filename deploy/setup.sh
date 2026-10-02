#!/usr/bin/env bash
# ChefControl SuperAdmin backend — first-time setup / redeploy on the same
# Ubuntu EC2 instance that runs chefcontrol-backend. Runs as its own service
# user, its own directory and its own port (8001), so it never touches the
# tenant-facing backend.
# Run as a user with sudo (e.g. the default `ubuntu` user).
#
# Usage: ./setup.sh
# Expects a filled-in /opt/chefcontrol-superadmin-backend/.env to already
# exist on redeploys; on first run it copies .env.example there for you to edit.

set -euo pipefail

REPO_URL="https://github.com/jorginhos10/superadmin-backEnd.git"
APP_DIR="/opt/chefcontrol-superadmin-backend"
SERVICE_USER="superadmin"

echo "==> Installing system packages"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip nginx git certbot python3-certbot-nginx

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  echo "==> Creating service user $SERVICE_USER"
  sudo useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

if [ -d "$APP_DIR/.git" ]; then
  echo "==> Pulling latest code"
  sudo -u "$SERVICE_USER" git -C "$APP_DIR" pull
else
  echo "==> Cloning repo"
  sudo mkdir -p "$APP_DIR"
  sudo chown "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"
  sudo -u "$SERVICE_USER" git clone "$REPO_URL" "$APP_DIR"
fi

echo "==> Setting up virtualenv"
sudo -u "$SERVICE_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "==> No .env found — copying .env.example, EDIT IT before starting the service"
  sudo -u "$SERVICE_USER" cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi

echo "==> Installing systemd service"
sudo cp "$APP_DIR/deploy/chefcontrol-superadmin-backend.service" /etc/systemd/system/chefcontrol-superadmin-backend.service
sudo systemctl daemon-reload
sudo systemctl enable chefcontrol-superadmin-backend

echo "==> Done."
echo "Next steps if this was a first-time setup:"
echo "  1. Edit $APP_DIR/.env with the real RDS host/credentials (separate DB from"
echo "     chefcontrol-backend) and a fresh JWT_SECRET."
echo "  2. sudo systemctl start chefcontrol-superadmin-backend"
echo "  3. Copy deploy/nginx-chefcontrol-superadmin-backend.conf to /etc/nginx/sites-available/,"
echo "     replace SUPERADMIN_API_DOMAIN, symlink into sites-enabled, then: sudo nginx -t && sudo systemctl reload nginx"
echo "  4. sudo certbot --nginx -d SUPERADMIN_API_DOMAIN"
echo "On redeploys, this script alone (after step 1) is enough — it restarts nothing,"
echo "so finish with: sudo systemctl restart chefcontrol-superadmin-backend"
