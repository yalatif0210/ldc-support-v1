"""
ticket_service.py — Création tickets, assignation agents, notifications Telegram.
"""
from models import Ticket, Agent, AgentStatus, TicketStatus, TicketPriority
from database import db
from datetime import datetime
import os
import httpx


def generate_ticket_ref() -> str:
    today = datetime.utcnow().strftime('%Y%m%d')
    count = Ticket.query.filter(Ticket.ticket_ref.like(f'TKT-{today}-%')).count() + 1
    return f"TKT-{today}-{count:04d}"


def assign_priority(category: str) -> TicketPriority:
    return TicketPriority.HIGH if category in ['Problème technique', 'Facturation'] else TicketPriority.MEDIUM


class TicketService:
    def __init__(self, db_instance):
        self.db = db_instance
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

    # ── Création ──────────────────────────────────────────────────────────────

    def create_ticket(self, client_name, client_chat_id, category, description) -> Ticket:
        agent = self._get_available_agent()

        ticket = Ticket(
            ticket_ref=generate_ticket_ref(),
            client_name=client_name,
            client_whatsapp=str(client_chat_id),
            category=category,
            description=description,
            priority=assign_priority(category),
            status=TicketStatus.IN_PROGRESS if agent else TicketStatus.OPEN,
            agent_id=agent.id if agent else None,
            queued=(agent is None),
        )
        self.db.session.add(ticket)

        if agent:
            agent.current_ticket_count += 1

        self.db.session.commit()

        if agent:
            self._notify_agent(agent, ticket)
            self._notify_client_in_progress(ticket, agent)
        else:
            self._notify_client_queued(ticket)

        return ticket

    # ── Prise en charge ───────────────────────────────────────────────────────

    def start_ticket(self, ticket: Ticket) -> Ticket:
        ticket.status = TicketStatus.IN_PROGRESS
        ticket.queued = False
        ticket.updated_at = datetime.utcnow()
        self.db.session.commit()
        if ticket.agent:
            self._notify_client_in_progress(ticket, ticket.agent)
        return ticket

    # ── Fermeture ─────────────────────────────────────────────────────────────

    def close_ticket(self, ticket: Ticket) -> Ticket:
        ticket.status = TicketStatus.CLOSED
        ticket.queued = False
        ticket.closed_at = datetime.utcnow()
        if ticket.agent:
            ticket.agent.current_ticket_count = max(0, ticket.agent.current_ticket_count - 1)
        self.db.session.commit()
        self._notify_client_closed(ticket)
        if ticket.agent:
            self._try_dequeue(ticket.agent)
        return ticket

    # ── Disponibilité ─────────────────────────────────────────────────────────

    def _get_available_agent(self) -> Agent | None:
        candidates = Agent.query.filter_by(is_active=True, status=AgentStatus.AVAILABLE).all()
        eligible = [a for a in candidates if a.has_capacity]
        if not eligible:
            return None
        return min(eligible, key=lambda a: a.current_ticket_count)

    def set_agent_status(self, agent: Agent, new_status: AgentStatus):
        agent.status = new_status
        self.db.session.commit()
        if new_status == AgentStatus.AVAILABLE:
            self._try_dequeue(agent)

    # ── File d'attente ────────────────────────────────────────────────────────

    def _try_dequeue(self, agent: Agent):
        if not (agent.is_active and agent.status == AgentStatus.AVAILABLE and agent.has_capacity):
            return
        queued_tickets = (
            Ticket.query
            .filter_by(queued=True, agent_id=None)
            .filter(Ticket.status == TicketStatus.OPEN)
            .order_by(Ticket.priority.desc(), Ticket.created_at.asc())
            .all()
        )
        for ticket in queued_tickets:
            if not agent.has_capacity:
                break
            ticket.agent_id = agent.id
            ticket.status = TicketStatus.IN_PROGRESS
            ticket.queued = False
            ticket.updated_at = datetime.utcnow()
            agent.current_ticket_count += 1
            self.db.session.commit()
            self._notify_agent(agent, ticket)
            self._notify_client_in_progress(ticket, agent)

    def process_queue(self):
        available_agents = Agent.query.filter_by(is_active=True, status=AgentStatus.AVAILABLE).all()
        for agent in available_agents:
            if agent.is_within_schedule and agent.has_capacity:
                self._try_dequeue(agent)

    # ── Notifications Telegram ────────────────────────────────────────────────

    def _notify_agent(self, agent: Agent, ticket: Ticket):
        if not agent.telegram_chat_id:
            return
        p_emoji = {
            TicketPriority.HIGH: '🔴',
            TicketPriority.MEDIUM: '🟡',
            TicketPriority.LOW: '🟢'
        }.get(ticket.priority, '⚪')
        msg = (
            f"🆕 <b>Nouveau Ticket Assigné</b>\n"
            f"{'─'*28}\n"
            f"🔖 Réf : <code>{ticket.ticket_ref}</code>\n"
            f"👤 Établissement : {ticket.client_name}\n"
            f"📂 Catégorie : {ticket.category}\n"
            f"{p_emoji} Priorité : {ticket.priority.value.upper()}\n"
            f"{'─'*28}\n"
            f"📝 <b>Description :</b>\n{ticket.description}\n"
            f"{'─'*28}\n"
            f"🕐 Créé le : {ticket.created_at.strftime('%d/%m/%Y à %H:%M')}\n\n"
            f"Pour prendre en charge : /prendre_{ticket.ticket_ref}\n"
            f"Pour fermer : /fermer_{ticket.ticket_ref}"
        )
        self._send_telegram(agent.telegram_chat_id, msg)

    def _notify_client_queued(self, ticket: Ticket):
        msg = (
            f"🕐 <b>Votre demande a bien été enregistrée.</b>\n\n"
            f"🔖 Référence : <code>{ticket.ticket_ref}</code>\n"
            f"📂 Catégorie : {ticket.category}\n\n"
            "Tous nos agents sont actuellement indisponibles. "
            "Votre ticket est en <b>file d'attente</b> et sera pris en charge "
            "dès qu'un agent sera disponible.\n\n"
            "Vous recevrez une notification à ce moment là. 🔔"
        )
        self._send_telegram(ticket.client_whatsapp, msg)

    def _notify_client_in_progress(self, ticket: Ticket, agent: Agent):
        msg = (
            f"🔵 <b>Votre demande est prise en charge !</b>\n\n"
            f"🔖 Référence : <code>{ticket.ticket_ref}</code>\n"
            f"👨‍💼 Agent : <b>{agent.name}</b>\n"
            f"📂 Catégorie : {ticket.category}\n\n"
            "Votre dossier est en cours de traitement. "
            "Nous revenons vers vous dans les plus brefs délais."
        )
        self._send_telegram(ticket.client_whatsapp, msg)

    def _notify_client_closed(self, ticket: Ticket):
        msg = (
            f"✅ <b>Votre ticket a été résolu !</b>\n\n"
            f"🔖 Référence : <code>{ticket.ticket_ref}</code>\n"
            f"📂 Catégorie : {ticket.category}\n\n"
            "Merci d'avoir contacté notre support.\n"
            "Tapez /start si vous avez besoin d'aide supplémentaire."
        )
        self._send_telegram(ticket.client_whatsapp, msg)

    def _send_telegram(self, chat_id: str, text: str):
        """Envoie un message Telegram via l'API Bot."""
        try:
            url = f"{self.api_url}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'HTML'
            }
            resp = httpx.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                print(f"❌ Telegram error: {resp.text} - ticket_service.py:198")
            else:
                print(f"✅ Message Telegram envoyé à {chat_id} - ticket_service.py:200")
        except Exception as e:
            print(f"❌ Erreur envoi Telegram à {chat_id}: {e} - ticket_service.py:202")