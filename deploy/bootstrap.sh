#!/usr/bin/env bash
# =============================================================================
# DevPilot - first-time setup on a fresh Ubuntu host.
#
#   curl -fsSL https://raw.githubusercontent.com/HaryiankKumra/devpilot/main/deploy/bootstrap.sh | bash
#
# Tested on Ubuntu 22.04 / 24.04 (x86_64 and ARM64), which is what Oracle
# Cloud's Always Free instances run. It installs Docker, opens the two ports
# that matter, clones the repository, and stops so you can fill in .env.prod.
# It does not start the stack: the first start needs real secrets, and a
# script that invents them for you is how a placeholder ends up in production.
#
# Idempotent: safe to run again.
# =============================================================================
set -euo pipefail

REPO="${DEVPILOT_REPO:-https://github.com/HaryiankKumra/devpilot.git}"
DIR="${DEVPILOT_DIR:-$HOME/devpilot}"

say() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

# --- 1. Docker -----------------------------------------------------------------
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  say "Docker and Compose already installed"
else
  say "Installing Docker Engine and the Compose plugin"
  sudo apt-get update -qq
  sudo apt-get install -y -qq ca-certificates curl gnupg
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  # shellcheck disable=SC1091
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

if ! id -nG "$USER" | grep -qw docker; then
  say "Adding $USER to the docker group (takes effect on next login)"
  sudo usermod -aG docker "$USER"
fi

# --- 2. Firewall ---------------------------------------------------------------
# Oracle's Ubuntu images ship iptables rules that drop everything except SSH,
# and those rules sit *in front of* Docker's. Opening the port in the OCI
# console security list is necessary but not sufficient; this half is the one
# people forget, and the symptom is a site that works from the VM and times
# out from everywhere else.
say "Opening ports 80 and 443 in the host firewall"
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 6 -p tcp --dport "$port" -j ACCEPT
  fi
done
# HTTP/3 is UDP 443. Harmless if unused.
if ! sudo iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null; then
  sudo iptables -I INPUT 6 -p udp --dport 443 -j ACCEPT
fi
# Persist across reboots.
if command -v netfilter-persistent >/dev/null 2>&1; then
  sudo netfilter-persistent save >/dev/null
else
  sudo apt-get install -y -qq iptables-persistent >/dev/null 2>&1 || true
  sudo netfilter-persistent save >/dev/null 2>&1 || true
fi

# --- 3. Source -----------------------------------------------------------------
if [ -d "$DIR/.git" ]; then
  say "Updating $DIR"
  git -C "$DIR" pull --ff-only
else
  say "Cloning into $DIR"
  git clone --depth 1 "$REPO" "$DIR"
fi

# --- 4. Configuration ----------------------------------------------------------
cd "$DIR"
if [ ! -f .env.prod ]; then
  cp .env.example .env.prod
  # Generate the one secret that must never be a placeholder.
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))' 2>/dev/null || openssl rand -base64 48 | tr -d '\n=/+')"
  sed -i "s|^DEVPILOT_SECRET_KEY=.*|DEVPILOT_SECRET_KEY=$SECRET|" .env.prod
  PGPASS="$(openssl rand -hex 24)"
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$PGPASS|" .env.prod
  say "Created .env.prod with a generated secret key and database password"
fi
mkdir -p secrets

cat <<EOF

$(printf '\033[1;32m')Host is ready.$(printf '\033[0m') Three things are yours to do, then start it:

  1. Edit $DIR/.env.prod -- at minimum:
       DEVPILOT_DOMAIN=your.duckdns.org
       DEVPILOT_LLM_MODE=gemini
       DEVPILOT_GEMINI_API_KEYS=...
       DEVPILOT_GITHUB_MODE=live and the GitHub App values
       DEVPILOT_GITHUB_OAUTH_REDIRECT_URI=https://your.duckdns.org/api/v1/github/callback
       DEVPILOT_FRONTEND_BASE_URL=https://your.duckdns.org
       DEVPILOT_CORS_ORIGINS=https://your.duckdns.org

  2. Copy the GitHub App private key to $DIR/secrets/github-app.pem
     and set DEVPILOT_GITHUB_APP_PRIVATE_KEY_PATH=/run/secrets/github-app.pem

  3. Open ports 80 and 443 in the OCI console too:
     Networking -> Virtual cloud networks -> your VCN -> Security lists
     -> Default -> Add ingress rules (source 0.0.0.0/0, TCP 80 and 443)

Then, from $DIR (log out and back in first so docker works without sudo):

  docker compose -f docker-compose.prod.yml up -d --build

EOF
