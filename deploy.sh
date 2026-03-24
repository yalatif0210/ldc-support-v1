#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# deploy.sh — Script de déploiement automatique sur VPS Ubuntu 22.04
#
# Usage : chmod +x deploy.sh && ./deploy.sh
# ══════════════════════════════════════════════════════════════════════════════

set -e  # Stopper en cas d'erreur

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[→]${NC} $1"; }

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║      WhatsApp Support Bot — Déploiement VPS      ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Vérifications préalables ─────────────────────────────────────────────────
info "Vérification des prérequis..."

[ "$EUID" -ne 0 ] && err "Lancez ce script avec sudo ou en tant que root"
[ ! -f ".env" ] && err "Fichier .env manquant. Copiez .env.example → .env et remplissez les valeurs."

# Vérifier que les clés Twilio sont renseignées
source .env
[ "$TWILIO_ACCOUNT_SID" = "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" ] && err "Renseignez votre TWILIO_ACCOUNT_SID dans .env"
[ "$TWILIO_AUTH_TOKEN" = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" ] && err "Renseignez votre TWILIO_AUTH_TOKEN dans .env"

log "Prérequis OK"

# ── Récupérer le domaine ─────────────────────────────────────────────────────
echo ""
read -p "Votre nom de domaine (ex: bot.monsite.com) : " DOMAIN
[ -z "$DOMAIN" ] && err "Domaine requis"

read -p "Votre email (pour Let's Encrypt) : " EMAIL
[ -z "$EMAIL" ] && err "Email requis"

# ── 1. Mise à jour système ───────────────────────────────────────────────────
info "Mise à jour du système..."
apt-get update -qq && apt-get upgrade -y -qq
log "Système mis à jour"

# ── 2. Installation Docker ───────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    info "Installation de Docker..."
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker $SUDO_USER 2>/dev/null || true
    log "Docker installé"
else
    log "Docker déjà installé ($(docker --version))"
fi

if ! docker compose version &>/dev/null; then
    info "Installation de Docker Compose plugin..."
    apt-get install -y docker-compose-plugin
    log "Docker Compose installé"
else
    log "Docker Compose déjà disponible"
fi

# ── 3. Pare-feu ──────────────────────────────────────────────────────────────
info "Configuration du pare-feu..."
ufw allow OpenSSH -q
ufw allow 80/tcp -q
ufw allow 443/tcp -q
ufw --force enable -q
log "Pare-feu configuré (SSH + HTTP + HTTPS)"

# ── 4. Configuration Nginx ───────────────────────────────────────────────────
info "Configuration Nginx pour le domaine $DOMAIN..."

# Remplacer VOTRE_DOMAINE.COM par le vrai domaine dans la config Nginx
sed -i "s/VOTRE_DOMAINE.COM/$DOMAIN/g" nginx/conf.d/support-bot.conf

log "Config Nginx mise à jour"

# ── 5. Lancer les services sans SSL d'abord (pour le challenge Certbot) ──────
info "Démarrage initial sans SSL (pour validation domaine)..."

# Config Nginx temporaire HTTP only pour Certbot
cat > nginx/conf.d/support-bot.conf << NGINXEOF
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        proxy_pass http://app:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
NGINXEOF

docker compose up -d db
info "Attente démarrage PostgreSQL..."
sleep 15

docker compose up -d app
info "Attente démarrage Flask..."
sleep 10

docker compose up -d nginx
log "Services démarrés"

# ── 6. Obtenir le certificat SSL ─────────────────────────────────────────────
info "Obtention du certificat SSL Let's Encrypt pour $DOMAIN..."

if ! command -v certbot &>/dev/null; then
    apt-get install -y certbot python3-certbot-nginx -qq
fi

certbot certonly \
    --webroot \
    --webroot-path=/var/lib/docker/volumes/$(basename $(pwd) | tr '[:upper:]' '[:lower:]' | tr -d '-')_certbot_www/_data \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    -d "$DOMAIN" \
    --non-interactive || warn "Certbot a rencontré une erreur. Vérifiez que $DOMAIN pointe vers ce serveur."

# ── 7. Config Nginx finale avec SSL ─────────────────────────────────────────
info "Activation de la configuration HTTPS..."

cat > nginx/conf.d/support-bot.conf << NGINXEOF
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name $DOMAIN;

    ssl_certificate     /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    location / {
        proxy_pass         http://app:5000;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
}
NGINXEOF

docker compose restart nginx
log "HTTPS activé"

# ── 8. Renouvellement SSL automatique ────────────────────────────────────────
info "Configuration du renouvellement SSL automatique..."
(crontab -l 2>/dev/null; echo "0 3 * * * certbot renew --quiet && docker compose restart nginx") | crontab -
log "Renouvellement SSL programmé (chaque jour à 3h du matin)"

# ── 9. Vérification finale ───────────────────────────────────────────────────
echo ""
info "Vérification du déploiement..."
sleep 5

HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5000/health)
if [ "$HTTP_STATUS" = "200" ]; then
    log "API Flask répond (HTTP $HTTP_STATUS)"
else
    warn "API Flask ne répond pas correctement (HTTP $HTTP_STATUS). Vérifiez : docker compose logs app"
fi

# ── Résumé ───────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  ✅  DÉPLOIEMENT TERMINÉ                                        ║"
echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║                                                                  ║"
echo "║  🌐  Dashboard  : https://$DOMAIN/dashboard/         "
echo "║  🔗  Webhook    : https://$DOMAIN/webhook/whatsapp   "
echo "║  🩺  Health     : https://$DOMAIN/health             "
echo "║                                                                  ║"
echo "║  👉  Prochaine étape :                                          ║"
echo "║      Collez l'URL webhook dans la console Twilio                ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
