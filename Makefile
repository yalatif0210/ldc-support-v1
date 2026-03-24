# ══════════════════════════════════════════════════════════════════════════════
# Makefile — Commandes utiles pour le WhatsApp Support Bot
# Usage : make <commande>
# ══════════════════════════════════════════════════════════════════════════════

.PHONY: help dev prod stop restart logs logs-app logs-db status shell-app shell-db backup restore clean update

# ── Aide ─────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  WhatsApp Support Bot — Commandes disponibles"
	@echo "  ─────────────────────────────────────────────"
	@echo "  make dev          → Lancer en mode développement"
	@echo "  make prod         → Lancer en mode production"
	@echo "  make stop         → Arrêter tous les services"
	@echo "  make restart      → Redémarrer tous les services"
	@echo "  make update       → Mettre à jour et redéployer"
	@echo "  make logs         → Voir tous les logs"
	@echo "  make logs-app     → Logs du bot uniquement"
	@echo "  make logs-db      → Logs PostgreSQL"
	@echo "  make status       → État des conteneurs"
	@echo "  make shell-app    → Shell dans le conteneur Flask"
	@echo "  make shell-db     → Console PostgreSQL"
	@echo "  make backup       → Sauvegarder la base de données"
	@echo "  make restore      → Restaurer une sauvegarde"
	@echo "  make clean        → Supprimer conteneurs et images"
	@echo "  make qr           → Générer le QR code d'accès"
	@echo ""

# ── Démarrage ────────────────────────────────────────────────────────────────
dev:
	@echo "🚀 Démarrage en mode développement..."
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
	@echo "✅ Disponible sur http://localhost:5000"

prod:
	@echo "🚀 Démarrage en mode production..."
	docker compose up -d --build
	@echo "✅ Services démarrés"
	@$(MAKE) status

# ── Contrôle ─────────────────────────────────────────────────────────────────
stop:
	docker compose down

restart:
	docker compose restart

update:
	@echo "🔄 Mise à jour du déploiement..."
	git pull
	docker compose build app
	docker compose up -d --no-deps app
	@echo "✅ Mise à jour effectuée"

# ── Logs ─────────────────────────────────────────────────────────────────────
logs:
	docker compose logs -f --tail=100

logs-app:
	docker compose logs -f --tail=100 app

logs-db:
	docker compose logs -f --tail=50 db

# ── Status ───────────────────────────────────────────────────────────────────
status:
	@echo ""
	@echo "  État des services :"
	@docker compose ps
	@echo ""
	@echo "  Test API :"
	@curl -s http://localhost:5000/health || echo "  ❌ API non joignable"
	@echo ""

# ── Shells ───────────────────────────────────────────────────────────────────
shell-app:
	docker compose exec app /bin/bash

shell-db:
	docker compose exec db psql -U support_user support_db

# ── Sauvegarde / Restauration ────────────────────────────────────────────────
backup:
	@mkdir -p ./backups
	@FILENAME="backups/backup_$$(date +%Y%m%d_%H%M%S).sql.gz"; \
	docker compose exec -T db pg_dump -U support_user support_db | gzip > $$FILENAME; \
	echo "✅ Sauvegarde créée : $$FILENAME"

restore:
	@echo "Fichiers disponibles :"
	@ls -la ./backups/*.sql.gz 2>/dev/null || echo "  Aucune sauvegarde trouvée"
	@read -p "Nom du fichier à restaurer : " FILE; \
	gunzip -c $$FILE | docker compose exec -T db psql -U support_user support_db; \
	echo "✅ Restauration terminée"

# ── Nettoyage ────────────────────────────────────────────────────────────────
clean:
	@echo "⚠️  Cela va supprimer les conteneurs et images (pas les volumes)."
	@read -p "Confirmer ? (oui/non) : " CONFIRM; \
	if [ "$$CONFIRM" = "oui" ]; then \
		docker compose down --rmi local; \
		echo "✅ Nettoyage effectué"; \
	else \
		echo "Annulé."; \
	fi

# ── QR Code ──────────────────────────────────────────────────────────────────
qr:
	docker compose exec app python generate_qr.py
