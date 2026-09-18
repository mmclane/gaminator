#!/usr/bin/env bash
# One-time setup on the VM: swap, Docker, the code, the .env, and the first start.
#
#   bash setup-vm.sh https://github.com/<you>/gaminator.git
#
# Safe to re-run: every step checks whether it has already been done.
set -euo pipefail

REPO="${1:-}"
DIR="${DIR:-$HOME/gaminator}"
SWAP_GB="${SWAP_GB:-2}"

if [[ -z "$REPO" && ! -d "$DIR/.git" ]]; then
  echo "Usage: bash setup-vm.sh <git clone URL>" >&2
  exit 1
fi

# --- swap: an e2-micro has 1 GB of RAM; building the image needs a little more headroom -----
if ! sudo swapon --show | grep -q '^/swapfile'; then
  echo "Creating a ${SWAP_GB} GB swap file..."
  sudo fallocate -l "${SWAP_GB}G" /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# --- docker (official repo, includes the compose plugin) -------------------------------------
if ! command -v docker >/dev/null; then
  echo "Installing Docker..."
  curl -fsSL https://get.docker.com | sudo sh
fi
if ! id -nG "$USER" | grep -qw docker; then
  sudo usermod -aG docker "$USER"
  NEED_RELOGIN=1
fi
sudo systemctl enable --now docker >/dev/null
sudo apt-get install -y -q git make >/dev/null

# --- the code ---------------------------------------------------------------------------------
if [[ ! -d "$DIR/.git" ]]; then
  git clone "$REPO" "$DIR"
fi
cd "$DIR"

# --- the .env -------------------------------------------------------------------------------
if [[ ! -f .env ]]; then
  echo
  echo "Creating .env. Paste values when asked (input is hidden for the token)."
  read -rsp "DISCORD_TOKEN: " token; echo
  read -rp  "GUILD_ID (server ID, or several separated by commas; blank = global sync): " guilds
  {
    echo "DISCORD_TOKEN=$token"
    echo "GUILD_ID=$guilds"
    echo "LOG_LEVEL=INFO"
  } > .env
  chmod 600 .env
  echo ".env written. Edit it later with: nano $DIR/.env"
fi

# --- start ----------------------------------------------------------------------------------
echo
echo "Building the image and starting the bot (a few minutes on an e2-micro)..."
sudo docker compose up -d --build
echo
sudo docker compose ps
cat <<MSG

Done. The bot restarts on its own after reboots. Useful commands (from $DIR):

  make logs        follow the bot's logs
  make restart     restart after editing .env
  bash deploy/gcp/update.sh   pull the latest code and rebuild
MSG
if [[ "${NEED_RELOGIN:-0}" == 1 ]]; then
  echo
  echo "Log out and back in once so 'docker' works without sudo (make targets rely on that)."
fi
