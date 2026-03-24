"""
bot_handler.py — Gestion du dialogue Telegram (client + commandes agent).
"""
from models import Conversation, Ticket, Agent, AgentStatus, TicketCategory, TicketStatus, AgentRole
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
    "4️⃣  Rapport hebdomadaire\n"
    "5️⃣  Autre\n\n"
    "Répondez avec le numéro (1 à 5) :"
)

WELCOME_MSG = (
    "👋 Bonjour ! Bienvenue au <b>Support Client LHSPLA-LDC</b>.\n\n"
    "Je vais vous aider à créer une demande d'assistance.\n\n"
    "Tapez /start pour recommencer à tout moment.\n\n"
    "Pour commencer, quel est votre <b>Établissement</b> ?"
)

AGENT_HELP_MSG = (
    "🛠️ <b>Commandes Agent disponibles :</b>\n\n"
    "━━━━━ <b>Statut</b> ━━━━━\n"
    "/dispo — 🟢 Passer en disponible\n"
    "/occupe — 🟡 Passer en occupé\n"
    "/absent — 🔴 Passer en absent\n"
    "/statut — Voir votre statut actuel\n\n"
    "━━━━━ <b>Tickets</b> ━━━━━\n"
    "/prendre_TKT-XXXX — Notifier le client\n"
    "/fermer_TKT-XXXX — Fermer un ticket\n"
    "/ticket_TKT-XXXX — Voir les détails\n"
    "/mestickets — Voir vos tickets en cours\n"
)

STATUS_LABELS = {
    AgentStatus.AVAILABLE: '🟢 Disponible',
    AgentStatus.BUSY:      '🟡 Occupé',
    AgentStatus.ABSENT:    '🔴 Absent',
}


class BotHandler:
    def __init__(self, db_instance, ticket_service):
        self.db = db_instance
        self.ticket_service = ticket_service

    def handle_message(self, chat_id: str, message: str, user_name: str = '') -> str:
        msg = message.strip()
        agent = Agent.query.filter_by(telegram_chat_id=chat_id, is_active=True).first()
        if agent:
            return self._handle_agent_message(agent, msg)
        return self._handle_client_message(chat_id, msg, user_name)

    # ═══════════════════════════════════════════════════════════════════════════
    # AGENT COMMANDS
    # ═══════════════════════════════════════════════════════════════════════════

    def _handle_agent_message(self, agent: Agent, message: str) -> str:
        msg = message.lower().strip()

        if msg in ['/dispo', 'dispo']:
            return self._agent_set_status(agent, AgentStatus.AVAILABLE)
        if msg in ['/occupe', 'occupe', '/occupé', 'occupé']:
            return self._agent_set_status(agent, AgentStatus.BUSY)
        if msg in ['/absent', 'absent']:
            return self._agent_set_status(agent, AgentStatus.ABSENT)
        if msg in ['/statut', 'statut']:
            return self._agent_my_status(agent)
        if msg in ['/mestickets', 'mestickets']:
            return self._agent_my_tickets(agent)
        if msg.startswith('/prendre_') or msg.startswith('prendre_'):
            ref = message.split('_', 1)[1].upper() if '_' in message else ''
            return self._agent_start_ticket(agent, ref)
        if msg.startswith('/fermer_') or msg.startswith('fermer_'):
            ref = message.split('_', 1)[1].upper() if '_' in message else ''
            return self._agent_close_ticket(agent, ref)
        if msg.startswith('/ticket_') or msg.startswith('ticket_'):
            ref = message.split('_', 1)[1].upper() if '_' in message else ''
            return self._agent_view_ticket(agent, ref)
        if msg in ['/start', '/aide', '/help']:
            return f"👋 Bonjour <b>{agent.name}</b> !\n\n" + AGENT_HELP_MSG

        return f"👋 Bonjour <b>{agent.name}</b> !\n\n" + AGENT_HELP_MSG

    def _agent_set_status(self, agent, new_status):
        old = STATUS_LABELS.get(agent.status, '—')
        self.ticket_service.set_agent_status(agent, new_status)
        new = STATUS_LABELS.get(new_status, '—')
        return f"✅ Statut mis à jour : {old} → {new}"

    def _agent_my_status(self, agent):
        return (
            f"📊 <b>Votre statut — {agent.name}</b>\n"
            f"• Statut : {STATUS_LABELS.get(agent.status, '—')}\n"
            f"• Horaires : {'✅ En horaire' if agent.is_within_schedule else '⛔ Hors horaire'}\n"
            f"• Charge : {agent.current_ticket_count}/{agent.max_tickets}\n"
            f"• Assignable : {'✅ Oui' if agent.is_truly_available else '❌ Non'}"
        )

    def _agent_start_ticket(self, agent, ticket_ref):
        ticket = Ticket.query.filter_by(ticket_ref=ticket_ref).first()
        if not ticket:
            return f"❌ Ticket <code>{ticket_ref}</code> introuvable."
        if ticket.agent_id != agent.id:
            return "❌ Ce ticket n'est pas assigné à vous."
        if ticket.status == TicketStatus.CLOSED:
            return "⚠️ Ce ticket est déjà fermé."
        self.ticket_service.start_ticket(ticket)
        return (
            f"🔵 Prise en charge confirmée ! Client notifié.\n\n"
            f"Pour fermer : /fermer_{ticket.ticket_ref}"
        )

    def _agent_close_ticket(self, agent, ticket_ref):
        ticket = Ticket.query.filter_by(ticket_ref=ticket_ref).first()
        if not ticket:
            return f"❌ Ticket <code>{ticket_ref}</code> introuvable."
        if ticket.agent_id != agent.id:
            return "❌ Ce ticket n'est pas assigné à vous."
        if ticket.status == TicketStatus.CLOSED:
            return "⚠️ Ce ticket est déjà fermé."
        self.ticket_service.close_ticket(ticket)
        return f"✅ Ticket <code>{ticket_ref}</code> fermé ! Client notifié. 📲"

    def _agent_view_ticket(self, agent, ticket_ref):
        ticket = Ticket.query.filter_by(ticket_ref=ticket_ref).first()
        if not ticket:
            return f"❌ Ticket <code>{ticket_ref}</code> introuvable."
        s = {'open': '🟡', 'in_progress': '🔵', 'closed': '✅'}.get(ticket.status.value, '⚪')
        p = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}.get(ticket.priority.value, '⚪')
        return (
            f"📋 <b>Détails du ticket</b>\n"
            f"🔖 Réf : <code>{ticket.ticket_ref}</code>\n"
            f"👤 Établissement : {ticket.client_name}\n"
            f"📂 Catégorie : {ticket.category}\n"
            f"{p} Priorité : {ticket.priority.value.upper()}\n"
            f"{s} Statut : {ticket.status.value.upper()}\n"
            f"📝 Description : {ticket.description}\n"
            f"🕐 Créé le : {ticket.created_at.strftime('%d/%m/%Y à %H:%M')}\n\n"
            f"Pour fermer : /fermer_{ticket.ticket_ref}"
        )

    def _agent_my_tickets(self, agent):
        tickets = (Ticket.query
                   .filter_by(agent_id=agent.id)
                   .filter(Ticket.status != TicketStatus.CLOSED)
                   .order_by(Ticket.created_at.desc()).all())
        if not tickets:
            return "✅ Vous n'avez aucun ticket en cours."
        lines = [f"📋 <b>Vos tickets en cours ({len(tickets)}) :</b>\n"]
        for t in tickets:
            p = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}.get(t.priority.value, '⚪')
            lines.append(
                f"{p} <code>{t.ticket_ref}</code> — {t.client_name}\n"
                f"   📂 {t.category}\n"
                f"   🕐 {t.created_at.strftime('%d/%m %H:%M')}"
            )
        lines.append("\nPour fermer : /fermer_TKT-XXXX-XXXX")
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════════════════════
    # CLIENT DIALOGUE
    # ═══════════════════════════════════════════════════════════════════════════

    def _handle_client_message(self, chat_id: str, message: str, user_name: str = '') -> str:
        if message in ['/start', '/aide', '/restart']:
            self._reset_conversation(chat_id)
            return WELCOME_MSG

        conv = Conversation.query.filter_by(client_whatsapp=chat_id).first()
        if not conv:
            conv = Conversation(client_whatsapp=chat_id, step='ask_name', temp_data={})
            self.db.session.add(conv)
            self.db.session.commit()
            return WELCOME_MSG

        handler = getattr(self, f'_step_{conv.step}', self._step_unknown)
        return handler(conv, message, user_name)

    def _step_ask_name(self, conv, message, user_name=''):
        if len(message) < 2:
            return "❌ Merci d'entrer un nom valide (au moins 2 caractères)."
        self._update_conv(conv, 'ask_category', {'client_name': message})
        return f"Merci <b>{message}</b> ! 😊\n\n" + CATEGORY_MENU

    def _step_ask_category(self, conv, message, user_name=''):
        if message not in CATEGORIES:
            return f"❌ Choix invalide.\n\n{CATEGORY_MENU}"
        category = CATEGORIES[message]
        self._update_conv(conv, 'ask_description', {'category': category})
        return (
            f"✅ Catégorie : <b>{category}</b>\n\n"
            "Décrivez maintenant votre problème en détail.\n"
            "<i>Donnez le maximum d'informations pour un traitement rapide</i>"
        )

    def _step_ask_description(self, conv, message, user_name=''):
        if len(message) < 10:
            return "❌ Description trop courte. Merci de décrire votre problème plus précisément."
        self._update_conv(conv, 'confirm', {'description': message})
        data = conv.temp_data
        return (
            "📋 <b>Récapitulatif de votre demande :</b>\n\n"
            f"👤 Établissement : <b>{data.get('client_name')}</b>\n"
            f"📂 Catégorie : <b>{data.get('category')}</b>\n"
            f"📝 Description : {data.get('description')}\n\n"
            "Confirmez-vous cette demande ?\n"
            "✅ Tapez <b>OUI</b> pour confirmer\n"
            "❌ Tapez <b>NON</b> pour recommencer"
        )

    def _step_confirm(self, conv, message, user_name=''):
        if message.upper() == 'NON':
            self._reset_conversation(conv.client_whatsapp)
            return "🔄 Annulé. Recommençons.\n\n" + WELCOME_MSG
        if message.upper() != 'OUI':
            return "Répondez <b>OUI</b> pour confirmer ou <b>NON</b> pour recommencer."
        try:
            data = conv.temp_data
            ticket = self.ticket_service.create_ticket(
                client_name=data['client_name'],
                client_chat_id=conv.client_whatsapp,
                category=data['category'],
                description=data['description']
            )
            self._update_conv(conv, 'done', {})
            if ticket.queued:
                return (
                    f"🕐 <b>Demande enregistrée en file d'attente</b>\n\n"
                    f"🔖 Référence : <code>{ticket.ticket_ref}</code>\n\n"
                    "Aucun agent disponible pour le moment. "
                    "Vous serez notifié dès la prise en charge."
                )
            return (
                f"🎉 <b>Votre ticket a été créé avec succès !</b>\n\n"
                f"🔖 Référence : <code>{ticket.ticket_ref}</code>\n"
                f"👨‍💼 Agent assigné : {ticket.agent.name if ticket.agent else '—'}\n\n"
                "Un agent traite votre demande. Vous serez contacté bientôt.\n\n"
                "Tapez /start pour une nouvelle demande."
            )
        except Exception as e:
            print(f"Erreur création ticket: {e} - bot_handler.py:254")
            return "❌ Une erreur est survenue. Veuillez réessayer."

    def _step_done(self, conv, message, user_name=''):
        return "✅ Votre demande est en cours.\n\nTapez /start pour une nouvelle demande."

    def _step_unknown(self, conv, message, user_name=''):
        self._reset_conversation(conv.client_whatsapp)
        return WELCOME_MSG

    def _update_conv(self, conv, new_step, new_data):
        conv.step = new_step
        merged = dict(conv.temp_data or {})
        merged.update(new_data)
        conv.temp_data = merged
        conv.updated_at = datetime.utcnow()
        self.db.session.commit()

    def _reset_conversation(self, chat_id):
        conv = Conversation.query.filter_by(client_whatsapp=chat_id).first()
        if conv:
            conv.step = 'ask_name'
            conv.temp_data = {}
            conv.updated_at = datetime.utcnow()
            self.db.session.commit()
        else:
            self.db.session.add(
                Conversation(client_whatsapp=chat_id, step='ask_name', temp_data={})
            )
            self.db.session.commit()