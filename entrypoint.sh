#!/bin/sh
# ══════════════════════════════════════════════════════════════════════════════
# entrypoint.sh — Initialise la base de données puis lance Gunicorn
# Exécuté automatiquement au démarrage du conteneur
# ══════════════════════════════════════════════════════════════════════════════

set -e

echo "⏳ Initialisation de la base de données..."

python -c "
from app import app
from database import init_db
with app.app_context():
    init_db()
print('✅ Base de données prête.')
"

echo "🚀 Démarrage de Gunicorn..."
exec gunicorn \
    --bind 0.0.0.0:5000 \
    --workers 2 \
    --timeout 60 \
    --access-logfile - \
    --error-logfile - \
    app:app
