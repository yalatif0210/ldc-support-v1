#!/bin/sh
set -e

echo "⏳ Initialisation de la base de données..."
python -c "
from app import app
from database import init_db
with app.app_context():
    init_db()
print('✅ Base de données prête.')
"

echo "🔗 Configuration du webhook Telegram..."
python -c "
import os, httpx
token = os.getenv('TELEGRAM_BOT_TOKEN', '')
base_url = os.getenv('BASE_URL', '')
if token and base_url:
    url = f'{base_url}/webhook/telegram/{token}'
    resp = httpx.post(f'https://api.telegram.org/bot{token}/setWebhook', json={'url': url})
    print(f'✅ Webhook Telegram configuré : {url}')
    print(f'   Réponse : {resp.json()}')
else:
    print('⚠️  TELEGRAM_BOT_TOKEN ou BASE_URL manquant — webhook non configuré')
"

echo "🚀 Démarrage de Gunicorn..."
exec gunicorn \
    --bind 0.0.0.0:5000 \
    --workers 2 \
    --timeout 60 \
    --access-logfile - \
    --error-logfile - \
    app:app