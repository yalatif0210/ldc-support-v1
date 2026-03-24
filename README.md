# 📲 WhatsApp Support Bot

Bot de gestion de tickets de support client via WhatsApp, avec dashboard web, système de disponibilité des agents et notifications automatiques.

---

## 🏗️ Architecture complète

```
                        INTERNET
                            │
                    ┌───────────────┐
                    │    Client     │
                    │  WhatsApp     │
                    └──────┬────────┘
                           │ Message
                           ▼
                    ┌───────────────┐
                    │    Twilio     │  (API WhatsApp)
                    └──────┬────────┘
                           │ Webhook HTTPS POST
                           ▼
        ┌──────────────────────────────────────────┐
        │               VPS Ubuntu 22.04           │
        │                                          │
        │  ┌─────────┐    ┌──────────────────────┐ │
        │  │  Nginx  │───▶│    Flask + Gunicorn  │ │
        │  │  (SSL)  │    │                      │ │
        │  └─────────┘    │  • BotHandler        │ │
        │                 │  • TicketService     │ │
        │                 │  • ReminderScheduler │ │
        │                 └──────────┬───────────┘ │
        │                            │              │
        │                    ┌───────┴──────┐       │
        │                    │  PostgreSQL  │       │
        │                    │   (Docker)   │       │
        │                    └──────────────┘       │
        └──────────────────────────────────────────┘
                           │
               ┌───────────┴───────────┐
               ▼                       ▼
        ┌─────────────┐       ┌─────────────────┐
        │   Agent     │       │     Client      │
        │  WhatsApp   │       │    WhatsApp     │
        │ (alertes)   │       │ (notifications) │
        └─────────────┘       └─────────────────┘
```

---

## ✅ Fonctionnalités implémentées

### 🤖 Bot conversationnel (client)
- Dialogue guidé en 5 étapes : nom → catégorie → description → confirmation
- 5 catégories : Problème technique, Coaching, Compte, Rappport, Autre
- Commande `AIDE` pour recommencer à tout moment
- Gestion de l'état de conversation en base de données

### 🎫 Gestion des tickets
- Référence unique générée automatiquement (`TKT-YYYYMMDD-XXXX`)
- Priorité automatique selon la catégorie (HIGH / MEDIUM / LOW)
- Statuts : `open` → `in_progress` → `closed`
- File d'attente si aucun agent disponible

### 📲 Notifications WhatsApp client
| Événement | Message envoyé |
|---|---|
| Ticket créé + agent dispo | 🎉 Confirmation + référence + nom agent |
| Ticket en file d'attente | 🕐 Mise en attente + référence |
| Prise en charge | 🔵 Nom de l'agent + début de traitement |
| Ticket résolu | ✅ Clôture + invitation à recontacter |

### 👨‍💼 Interface agent (2 canaux)

**Via WhatsApp :**
| Commande | Action |
|---|---|
| `DISPO` | 🟢 Passer en disponible |
| `OCCUPE` | 🟡 Passer en occupé |
| `ABSENT` | 🔴 Passer en absent |
| `MON STATUT` | Voir sa disponibilité détaillée |
| `PRENDRE TKT-XXX` | Notifier le client de la prise en charge |
| `FERMER TKT-XXX` | Fermer le ticket + notifier le client |
| `TICKET TKT-XXX` | Voir les détails d'un ticket |
| `MES TICKETS` | Lister ses tickets en cours |
| `FILE ATTENTE` | Voir les tickets sans agent (admin) |

**Via Dashboard web :**
- Login sécurisé JWT (email + mot de passe)
- Vue filtrée par agent (chaque agent voit ses tickets)
- Vue globale admin (tous les tickets)
- Boutons Prendre en charge / Fermer
- Gestion des agents et de leurs statuts
- File d'attente dédiée (admin)
- QR code d'accès téléchargeable

### 🟢 Système de disponibilité
Un agent est assignable seulement si les **3 conditions** sont réunies :
1. **Statut manuel** = AVAILABLE (pas BUSY ou ABSENT)
2. **Dans ses horaires** de travail (configurable par agent)
3. **Capacité** : tickets en cours < limite max (5 par défaut)

### ⏰ Scheduler automatique
- Vérification des tickets en retard toutes les **30 minutes**
- Traitement de la file d'attente toutes les **5 minutes**
- Délais de relance : 1h (HIGH) / 4h (MEDIUM) / 24h (LOW)
- Escalade au manager après 3 relances sans réponse

### 🔐 Authentification
- JWT (valide 8h)
- Deux rôles : `agent` et `admin`
- Hachage bcrypt des mots de passe
- Changement de mot de passe depuis le dashboard

---

## 📁 Structure du projet

```
whatsapp-support-bot/
│
├── app.py                  # Application Flask principale + routes API
├── bot_handler.py          # Machine à états dialogue client + commandes agent
├── ticket_service.py       # Création tickets, assignation, notifications
├── scheduler.py            # Relances automatiques + traitement file d'attente
├── models.py               # Modèles SQLAlchemy (Agent, Ticket, Conversation)
├── database.py             # Init DB + comptes de démonstration
├── generate_qr.py          # Générateur QR code WhatsApp
│
├── dashboard/
│   └── index.html          # Dashboard web (login, tickets, agents, QR)
│
├── nginx/
│   ├── nginx.conf          # Configuration Nginx principale
│   └── conf.d/
│       └── support-bot.conf  # Virtual host + SSL
│
├── docker-compose.yml      # Stack production (app + db + nginx)
├── docker-compose.dev.yml  # Override développement
├── Dockerfile              # Image Flask optimisée
├── requirements.txt        # Dépendances Python
├── Makefile                # Commandes utiles
├── deploy.sh               # Script de déploiement automatique VPS
└── .env.example            # Template de configuration
```

---

## 🗃️ Base de données

### Table `agents`
| Colonne | Type | Description |
|---|---|---|
| id | INTEGER | Clé primaire |
| name | VARCHAR | Nom complet |
| email | VARCHAR | Email (login) |
| password_hash | VARCHAR | Mot de passe haché |
| whatsapp_number | VARCHAR | Numéro WhatsApp |
| role | ENUM | agent / admin |
| status | ENUM | available / busy / absent |
| max_tickets | INTEGER | Limite de tickets simultanés |
| current_ticket_count | INTEGER | Tickets ouverts actuels |
| work_schedule | JSON | Horaires par jour de la semaine |
| timezone | VARCHAR | Fuseau horaire |

### Table `tickets`
| Colonne | Type | Description |
|---|---|---|
| id | INTEGER | Clé primaire |
| ticket_ref | VARCHAR | Référence unique (TKT-YYYYMMDD-XXXX) |
| client_name | VARCHAR | Nom du client |
| client_whatsapp | VARCHAR | Numéro WhatsApp du client |
| category | VARCHAR | Catégorie du problème |
| description | TEXT | Description complète |
| priority | ENUM | low / medium / high |
| status | ENUM | open / in_progress / closed |
| agent_id | FK | Agent assigné |
| queued | BOOLEAN | En file d'attente |
| reminder_count | INTEGER | Nombre de relances envoyées |

### Table `conversations`
| Colonne | Type | Description |
|---|---|---|
| client_whatsapp | VARCHAR | Numéro client |
| step | VARCHAR | Étape du dialogue en cours |
| temp_data | JSON | Données collectées (nom, catégorie…) |

---

## 🚀 Déploiement

### Développement local

```bash
# 1. Cloner et configurer
git clone <repo>
cd whatsapp-support-bot
cp .env.example .env
# Remplir .env avec vos clés Twilio

# 2. Lancer en mode dev (hot-reload, ports exposés)
make dev
# ou : docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# 3. Exposer le webhook avec ngrok
ngrok http 5000
# Copier l'URL HTTPS dans la console Twilio

# 4. Accéder au dashboard
# http://localhost:5000  → API
# dashboard/index.html  → Ouvrir directement dans le navigateur
```

### Production VPS (Ubuntu 22.04)

```bash
# 1. Se connecter au VPS
ssh root@VOTRE_IP

# 2. Uploader le projet
scp -r ./whatsapp-support-bot root@VOTRE_IP:/opt/
cd /opt/whatsapp-support-bot

# 3. Configurer
cp .env.example .env
nano .env  # Remplir toutes les valeurs

# 4. Déploiement automatique (Docker + Nginx + SSL)
chmod +x deploy.sh
./deploy.sh
# → Entrez votre domaine et email quand demandé

# 5. Configurer le webhook dans Twilio
# Console Twilio → Messaging → Sandbox Settings
# When a message comes in : https://VOTRE_DOMAINE/webhook/whatsapp
```

### Commandes de gestion quotidienne

```bash
make status          # État des services
make logs-app        # Logs du bot en temps réel
make restart         # Redémarrer
make backup          # Sauvegarder la base de données
make update          # Mettre à jour (git pull + rebuild)
make shell-db        # Accéder à PostgreSQL
make qr              # Générer le QR code
```

---

## 🔑 Comptes par défaut

| Email | Mot de passe | Rôle |
|---|---|---|
| admin@support.com | Admin1234! | 👑 Administrateur |
| alice@support.com | Alice1234! | 🎧 Agent |
| bob@support.com | Bob1234! | 🎧 Agent |
| claire@support.com | Claire1234! | 🎧 Agent |

> ⚠️ Changez ces mots de passe immédiatement en production via le dashboard → Mon profil.

---

## 📡 API REST

| Méthode | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/login` | ❌ | Connexion |
| GET | `/api/auth/me` | ✅ | Profil courant |
| GET | `/api/tickets` | ✅ | Liste tickets (filtrée par rôle) |
| PUT | `/api/tickets/:id/start` | ✅ | Prise en charge |
| PUT | `/api/tickets/:id/close` | ✅ | Fermeture |
| GET | `/api/tickets/queue` | Admin | File d'attente |
| GET | `/api/stats` | ✅ | Statistiques |
| PUT | `/api/agents/me/status` | ✅ | Changer son statut |
| GET | `/api/agents` | Admin | Liste agents |
| POST | `/api/agents` | Admin | Créer un agent |
| PUT | `/api/agents/:id/schedule` | Admin | Horaires + limite tickets |
| GET | `/api/qr-info` | ✅ | Infos QR code |
| POST | `/webhook/whatsapp` | ❌ | Webhook Twilio |
| GET | `/health` | ❌ | Santé du service |
