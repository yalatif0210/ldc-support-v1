from models import Conversation, Ticket, Agent, AgentStatus, TicketCategory, TicketStatus
from database import db
from datetime import datetime

CATEGORIES = {
    '1': TicketCategory.TECHNICAL.value,
    '2': TicketCategory.BILLING.value,
    '3': TicketCategory.ACCOUNT.value,
    '4': TicketCategory.DELIVERY.value,
    '5': TicketCategory.OTHER.value,
}

CATEGORY_MENU = (
    "Choisissez la catégorie de votre demande :\n\n"
    "1️⃣  Problème technique\n"
    "2️⃣  Coaching\n"
    "3️⃣  Compte utilisateur\n"
    "4️⃣  Rapport / Hebdo\n"
    "5️⃣  Autre\n\n"
    "Répondez avec le numéro (1 à 5) :"
)

WELCOME_MSG = (
    "👋 Bonjour ! Bienvenue au *Support Client LHSPLA-LDC*.\n\n"
    "Je vais vous aider à créer une demande d'assistance. "
    "Cela ne prendra que quelques secondes.\n\n"
    "Tapez *AIDE* pour recommencer à tout moment.\n\n"
    "Pour commencer, quel est votre *nom complet* ?"
)

AGENT_HELP_MSG = (
    "🛠️ *Commandes Agent disponibles :*\n\n"
    "━━━━━ *Statut* ━━━━━\n"
    "• `DISPO` — 🟢 Passer en disponible\n"
    "• `OCCUPE` — 🟡 Passer en occupé\n"
    "• `ABSENT` — 🔴 Passer en absent\n"
    "• `MON STATUT` — Voir votre statut actuel\n\n"
    "━━━━━ *Tickets* ━━━━━\n"
    "• `PRENDRE TKT-XXXX` — Notifier le client de la prise en charge\n"
    "• `FERMER TKT-XXXX` — Fermer un ticket résolu\n"
    "• `TICKET TKT-XXXX` — Voir les détails d'un ticket\n"
    "• `MES TICKETS` — Voir vos tickets en cours\n"
    "• `FILE ATTENTE` — Voir les tickets en attente (admin)\n\n"
    "• `AIDE AGENT` — Afficher cette aide\n"
)

STATUS_MAP = {
    'DISPO': AgentStatus.AVAILABLE,
    'DISPONIBLE': AgentStatus.AVAILABLE,
    'OCCUPE': AgentStatus.BUSY,
    'OCCUPÉ': AgentStatus.BUSY,
    'ABSENT': AgentStatus.ABSENT,
}

STATUS_LABELS = {
    AgentStatus.AVAILABLE: '🟢 Disponible',
    AgentStatus.BUSY:      '🟡 Occupé',
    AgentStatus.ABSENT:    '🔴 Absent',
}


class BotHandler:
    def __init__(self, db_instance, ticket_service):
        self.db = db_instance
        self.ticket_service = ticket_service

    def handle_message(self, sender: str, message: str) -> str:
        msg = message.strip()
        agent = Agent.query.filter_by(whatsapp_number=sender, is_active=True).first()
        if agent:
            return self._handle_agent_message(agent, msg)
        return self._handle_client_message(sender, msg)

    # ═══════════════════════════════════════════════════════════════════════════
    # AGENT COMMANDS
    # ═══════════════════════════════════════════════════════════════════════════

    def _handle_agent_message(self, agent: Agent, message: str) -> str:
        upper = message.upper().strip()

        # ── Statut ────────────────────────────────────────────────────────────
        if upper in STATUS_MAP:
            return self._agent_set_status(agent, STATUS_MAP[upper])

        if upper in ['MON STATUT', 'STATUT', 'STATUS']:
            return self._agent_my_status(agent)

        # ── Tickets ───────────────────────────────────────────────────────────
        if upper.startswith('PRENDRE '):
            return self._agent_start_ticket(agent, message[8:].strip().upper())
        if upper.startswith('FERMER '):
            return self._agent_close_ticket(agent, message[7:].strip().upper())
        if upper.startswith('TICKET '):
            return self._agent_view_ticket(agent, message[7:].strip().upper())
        if upper in ['MES TICKETS', 'MES_TICKETS']:
            return self._agent_my_tickets(agent)
        if upper in ['FILE ATTENTE', 'FILE_ATTENTE', 'QUEUE']:
            return self._agent_view_queue(agent)
        if upper in ['AIDE AGENT', 'AIDE', 'HELP']:
            return AGENT_HELP_MSG

        return f"👋 Bonjour *{agent.name}* !\n\n" + AGENT_HELP_MSG

    # ── Gestion du statut ─────────────────────────────────────────────────────

    def _agent_set_status(self, agent: Agent, new_status: AgentStatus) -> str:
        old_label = STATUS_LABELS.get(agent.status, '—')
        self.ticket_service.set_agent_status(agent, new_status)
        new_label = STATUS_LABELS.get(new_status, '—')

        msg = f"✅ Statut mis à jour : {old_label} → {new_label}\n"

        if new_status == AgentStatus.AVAILABLE:
            queued = Ticket.query.filter_by(queued=True, agent_id=None).count()
            msg += f"\n📬 {queued} ticket(s) en file d'attente ont été vérifiés." if queued else "\n📭 Aucun ticket en attente."
        elif new_status == AgentStatus.BUSY:
            msg += "\nVous ne recevrez plus de nouveaux tickets automatiquement."
        elif new_status == AgentStatus.ABSENT:
            msg += f"\nVous avez *{agent.current_ticket_count}* ticket(s) en cours non fermé(s)."

        return msg

    def _agent_my_status(self, agent: Agent) -> str:
        status_label = STATUS_LABELS.get(agent.status, '—')
        schedule_ok = '✅ Dans les horaires' if agent.is_within_schedule else '⛔ Hors horaires'
        capacity = f"{agent.current_ticket_count}/{agent.max_tickets} tickets"
        truly = '🟢 Oui' if agent.is_truly_available else '🔴 Non'

        return (
            f"📊 *Votre statut — {agent.name}*\n{'─'*28}\n"
            f"• Statut manuel : {status_label}\n"
            f"• Horaires : {schedule_ok}\n"
            f"• Charge : {capacity}\n"
            f"• Réellement disponible : {truly}\n"
        )

    # ── Tickets ───────────────────────────────────────────────────────────────

    def _agent_start_ticket(self, agent: Agent, ticket_ref: str) -> str:
        ticket = Ticket.query.filter_by(ticket_ref=ticket_ref).first()
        if not ticket:
            return f"❌ Ticket `{ticket_ref}` introuvable."
        if ticket.agent_id != agent.id:
            return f"❌ Ce ticket n'est pas assigné à vous."
        if ticket.status == TicketStatus.CLOSED:
            return f"⚠️ Le ticket `{ticket_ref}` est déjà fermé."
        if ticket.status == TicketStatus.IN_PROGRESS:
            return f"ℹ️ Déjà en cours. Le client a déjà été notifié."
        self.ticket_service.start_ticket(ticket)
        return (
            f"🔵 *Prise en charge confirmée !*\n\n"
            f"🔖 Réf : `{ticket.ticket_ref}`\n"
            f"👤 Client : {ticket.client_name}\n\n"
            f"✅ Le client a été notifié.\n\n"
            f"Pour fermer : `FERMER {ticket.ticket_ref}`"
        )

    def _agent_close_ticket(self, agent: Agent, ticket_ref: str) -> str:
        ticket = Ticket.query.filter_by(ticket_ref=ticket_ref).first()
        if not ticket:
            return f"❌ Ticket `{ticket_ref}` introuvable."
        if ticket.agent_id != agent.id:
            return f"❌ Ce ticket n'est pas assigné à vous."
        if ticket.status == TicketStatus.CLOSED:
            return f"⚠️ Le ticket `{ticket_ref}` est déjà fermé."
        self.ticket_service.close_ticket(ticket)
        return (
            f"✅ *Ticket fermé !*\n\n"
            f"🔖 Réf : `{ticket.ticket_ref}`\n"
            f"👤 Client : {ticket.client_name}\n\n"
            "Le client a été notifié. 📲"
        )

    def _agent_view_ticket(self, agent: Agent, ticket_ref: str) -> str:
        ticket = Ticket.query.filter_by(ticket_ref=ticket_ref).first()
        if not ticket:
            return f"❌ Ticket `{ticket_ref}` introuvable."
        s_emoji = {'open':'🟡','in_progress':'🔵','closed':'✅'}.get(ticket.status.value,'⚪')
        p_emoji = {'high':'🔴','medium':'🟡','low':'🟢'}.get(ticket.priority.value,'⚪')
        queue_line = "📬 *En file d'attente*\n" if ticket.queued else ""
        return (
            f"📋 *Détails du ticket*\n{'─'*28}\n"
            f"{queue_line}"
            f"🔖 Réf : `{ticket.ticket_ref}`\n"
            f"👤 Client : {ticket.client_name}\n"
            f"📞 Contact : {ticket.client_whatsapp.replace('whatsapp:','')}\n"
            f"📂 Catégorie : {ticket.category}\n"
            f"{p_emoji} Priorité : {ticket.priority.value.upper()}\n"
            f"{s_emoji} Statut : {ticket.status.value.upper()}\n"
            f"{'─'*28}\n"
            f"📝 *Description :*\n{ticket.description}\n"
            f"{'─'*28}\n"
            f"🕐 Créé le : {ticket.created_at.strftime('%d/%m/%Y à %H:%M')}\n\n"
            f"Pour fermer : `FERMER {ticket.ticket_ref}`"
        )

    def _agent_my_tickets(self, agent: Agent) -> str:
        tickets = (Ticket.query
                   .filter_by(agent_id=agent.id)
                   .filter(Ticket.status != TicketStatus.CLOSED)
                   .order_by(Ticket.created_at.desc()).all())
        if not tickets:
            return "✅ Vous n'avez aucun ticket en cours."
        lines = [f"📋 *Vos tickets en cours ({len(tickets)}/{agent.max_tickets}) :*\n"]
        for t in tickets:
            p = {'high':'🔴','medium':'🟡','low':'🟢'}.get(t.priority.value,'⚪')
            lines.append(f"{p} `{t.ticket_ref}`\n   👤 {t.client_name} — {t.category}\n   🕐 {t.created_at.strftime('%d/%m %H:%M')}\n")
        lines.append("Pour fermer : `FERMER TKT-XXXX-XXXX`")
        return "\n".join(lines)

    def _agent_view_queue(self, agent: Agent) -> str:
        from models import AgentRole
        if agent.role != AgentRole.ADMIN:
            return "❌ Commande réservée aux administrateurs."
        queued = (Ticket.query.filter_by(queued=True)
                  .filter(Ticket.status == TicketStatus.OPEN)
                  .order_by(Ticket.priority.desc(), Ticket.created_at.asc()).all())
        if not queued:
            return "📭 Aucun ticket en file d'attente. ✅"
        lines = [f"📬 *File d'attente ({len(queued)} tickets) :*\n"]
        for t in queued:
            p = {'high':'🔴','medium':'🟡','low':'🟢'}.get(t.priority.value,'⚪')
            wait = int((datetime.utcnow() - t.created_at).total_seconds() / 60)
            lines.append(f"{p} `{t.ticket_ref}` — {t.client_name}\n   ⏱ Attend depuis {wait} min\n")
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════════════════════
    # CLIENT DIALOGUE
    # ═══════════════════════════════════════════════════════════════════════════

    def _handle_client_message(self, sender: str, message: str) -> str:
        if message.upper() in ['AIDE', 'HELP', 'RESTART', 'RECOMMENCER']:
            self._reset_conversation(sender)
            return WELCOME_MSG
        conv = Conversation.query.filter_by(client_whatsapp=sender).first()
        if not conv:
            conv = Conversation(client_whatsapp=sender, step='ask_name', temp_data={})
            self.db.session.add(conv)
            self.db.session.commit()
            return WELCOME_MSG
        handler = getattr(self, f'_step_{conv.step}', self._step_unknown)
        return handler(conv, message)

    def _step_ask_name(self, conv, message):
        if len(message) < 2:
            return "❌ Merci d'entrer un nom valide (au moins 2 caractères)."
        self._update_conv(conv, 'ask_category', {'client_name': message})
        return f"Merci *{message}* ! 😊\n\n" + CATEGORY_MENU

    def _step_ask_category(self, conv, message):
        if message not in CATEGORIES:
            return f"❌ Choix invalide.\n\n{CATEGORY_MENU}"
        category = CATEGORIES[message]
        self._update_conv(conv, 'ask_description', {'category': category})
        return (f"✅ Catégorie : *{category}*\n\n"
                "Décrivez maintenant votre problème en détail.\n"
                "_(Donnez le maximum d'informations pour un traitement rapide)_")

    def _step_ask_description(self, conv, message):
        if len(message) < 10:
            return "❌ Description trop courte. Merci de décrire votre problème plus précisément."
        self._update_conv(conv, 'confirm', {'description': message})
        data = conv.temp_data
        return (
            "📋 *Récapitulatif de votre demande :*\n\n"
            f"👤 Nom : *{data.get('client_name')}*\n"
            f"📂 Catégorie : *{data.get('category')}*\n"
            f"📝 Description : {data.get('description')}\n\n"
            "Confirmez-vous cette demande ?\n"
            "✅ Tapez *OUI* pour confirmer\n"
            "❌ Tapez *NON* pour recommencer"
        )

    def _step_confirm(self, conv, message):
        if message.upper() == 'NON':
            self._reset_conversation(conv.client_whatsapp)
            return "🔄 Annulé. Recommençons.\n\n" + WELCOME_MSG
        if message.upper() != 'OUI':
            return "Répondez *OUI* pour confirmer ou *NON* pour recommencer."
        try:
            data = conv.temp_data
            ticket = self.ticket_service.create_ticket(
                client_name=data['client_name'],
                client_whatsapp=conv.client_whatsapp,
                category=data['category'],
                description=data['description']
            )
            self._update_conv(conv, 'done', {})
            if ticket.queued:
                return (
                    f"🕐 *Demande enregistrée en file d'attente*\n\n"
                    f"🔖 Référence : `{ticket.ticket_ref}`\n\n"
                    "Aucun agent n'est disponible pour le moment. "
                    "Vous serez notifié dès la prise en charge.\n\n"
                    "Tapez *AIDE* pour créer une nouvelle demande."
                )
            return (
                f"🎉 *Votre ticket a été créé avec succès !*\n\n"
                f"🔖 Référence : `{ticket.ticket_ref}`\n"
                f"👨‍💼 Agent assigné : {ticket.agent.name if ticket.agent else '—'}\n\n"
                "Un agent traite votre demande. Vous serez contacté bientôt.\n\n"
                "Tapez *AIDE* pour créer une nouvelle demande."
            )
        except Exception as e:
            print(f"Erreur création ticket: {e} - bot_handler.py:305")
            return "❌ Une erreur est survenue. Veuillez réessayer."

    def _step_done(self, conv, message):
        return "✅ Votre demande est en cours.\n\nTapez *AIDE* pour une nouvelle demande."

    def _step_unknown(self, conv, message):
        self._reset_conversation(conv.client_whatsapp)
        return WELCOME_MSG

    def _update_conv(self, conv, new_step, new_data):
        conv.step = new_step
        merged = dict(conv.temp_data or {})
        merged.update(new_data)
        conv.temp_data = merged
        conv.updated_at = datetime.utcnow()
        self.db.session.commit()

    def _reset_conversation(self, sender):
        conv = Conversation.query.filter_by(client_whatsapp=sender).first()
        if conv:
            conv.step = 'ask_name'
            conv.temp_data = {}
            conv.updated_at = datetime.utcnow()
            self.db.session.commit()
        else:
            self.db.session.add(Conversation(client_whatsapp=sender, step='ask_name', temp_data={}))
            self.db.session.commit()
