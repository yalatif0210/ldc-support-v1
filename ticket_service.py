from models import Ticket, Agent, AgentStatus, TicketStatus, TicketPriority
from database import db
from datetime import datetime
import os


def generate_ticket_ref() -> str:
    today = datetime.utcnow().strftime('%Y%m%d')
    count = Ticket.query.filter(Ticket.ticket_ref.like(f'TKT-{today}-%')).count() + 1
    return f"TKT-{today}-{count:04d}"


def assign_priority(category: str) -> TicketPriority:
    return TicketPriority.HIGH if category in ['Problème technique', 'Compte utilisateur'] else TicketPriority.MEDIUM
    

class TicketService:
    def __init__(self, db_instance, twilio_client):
        self.db = db_instance
        self.twilio_client = twilio_client
        self.twilio_from = os.getenv('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')

    # ── Création ──────────────────────────────────────────────────────────────

    def create_ticket(self, client_name, client_whatsapp, category, description) -> Ticket:
        agent = self._get_available_agent()

        ticket = Ticket(
            ticket_ref=generate_ticket_ref(),
            client_name=client_name,
            client_whatsapp=client_whatsapp,
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
            # Pas d'agent dispo → mettre en file d'attente et informer le client
            self._notify_client_queued(ticket)

        return ticket

    # ── Prise en charge manuelle ──────────────────────────────────────────────

    def start_ticket(self, ticket: Ticket) -> Ticket:
        if ticket.status == TicketStatus.IN_PROGRESS:
            return ticket
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
        # Tenter d'assigner un ticket en attente à l'agent qui vient de se libérer
        if ticket.agent:
            self._try_dequeue(ticket.agent)
        return ticket

    # ── Disponibilité agent ───────────────────────────────────────────────────

    def _get_available_agent(self) -> Agent | None:
        """
        Retourne le meilleur agent disponible :
        - Statut AVAILABLE
        - Dans ses horaires de travail
        - N'a pas atteint sa limite de tickets
        - Trié par charge croissante
        """
        candidates = Agent.query.filter_by(is_active=True, status=AgentStatus.AVAILABLE).all()
        eligible = [a for a in candidates if a.is_within_schedule and a.has_capacity]
        if not eligible:
            return None
        return min(eligible, key=lambda a: a.current_ticket_count)

    def set_agent_status(self, agent: Agent, new_status: AgentStatus):
        """Change le statut de l'agent et traite la file d'attente si disponible."""
        agent.status = new_status
        self.db.session.commit()
        if new_status == AgentStatus.AVAILABLE:
            self._try_dequeue(agent)

    # ── File d'attente ────────────────────────────────────────────────────────

    def _try_dequeue(self, agent: Agent):
        """Assigne les tickets en file d'attente à un agent qui vient de se libérer."""
        if not agent.is_truly_available:
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
            print(f"📬 Ticket {ticket.ticket_ref} sorti de la file → {agent.name} - ticket_service.py:132")

    def process_queue(self):
        """Passe en revue tous les agents disponibles et vide la file d'attente.
        Appelé périodiquement par le scheduler."""
        available_agents = Agent.query.filter_by(is_active=True, status=AgentStatus.AVAILABLE).all()
        for agent in available_agents:
            if agent.is_within_schedule and agent.has_capacity:
                self._try_dequeue(agent)

    # ── Notifications WhatsApp ────────────────────────────────────────────────

    def _notify_agent(self, agent: Agent, ticket: Ticket):
        p_emoji = {TicketPriority.HIGH:'🔴', TicketPriority.MEDIUM:'🟡', TicketPriority.LOW:'🟢'}.get(ticket.priority,'⚪')
        msg = (
            f"🆕 *Nouveau Ticket Assigné*\n{'─'*28}\n"
            f"🔖 Réf : `{ticket.ticket_ref}`\n"
            f"👤 Client : {ticket.client_name}\n"
            f"📞 WhatsApp : {ticket.client_whatsapp.replace('whatsapp:','')}\n"
            f"📂 Catégorie : {ticket.category}\n"
            f"{p_emoji} Priorité : {ticket.priority.value.upper()}\n{'─'*28}\n"
            f"📝 *Description :*\n{ticket.description}\n{'─'*28}\n"
            f"🕐 Créé le : {ticket.created_at.strftime('%d/%m/%Y à %H:%M')}\n\n"
            f"Tapez `PRENDRE {ticket.ticket_ref}` pour notifier le client."
        )
        self._send_whatsapp(agent.whatsapp_number, msg)

    def _notify_client_queued(self, ticket: Ticket):
        """Informe le client qu'aucun agent n'est disponible immédiatement."""
        msg = (
            f"🕐 *Votre demande a bien été enregistrée.*\n\n"
            f"🔖 Référence : `{ticket.ticket_ref}`\n"
            f"📂 Catégorie : {ticket.category}\n\n"
            "Tous nos agents sont actuellement indisponibles. "
            "Votre ticket est en *file d'attente* et sera pris en charge "
            "dès qu'un agent sera disponible.\n\n"
            "Vous recevrez une notification à ce moment-là. 🔔"
        )
        self._send_whatsapp(ticket.client_whatsapp, msg)

    def _notify_client_in_progress(self, ticket: Ticket, agent: Agent):
        msg = (
            f"🔵 *Votre demande est prise en charge !*\n\n"
            f"🔖 Référence : `{ticket.ticket_ref}`\n"
            f"👨‍💼 Agent : *{agent.name}*\n"
            f"📂 Catégorie : {ticket.category}\n\n"
            "Votre dossier est en cours de traitement. "
            "Nous revenons vers vous dans les plus brefs délais.\n\n"
            "_Tapez *AIDE* pour soumettre une nouvelle demande._"
        )
        self._send_whatsapp(ticket.client_whatsapp, msg)

    def _notify_client_closed(self, ticket: Ticket):
        msg = (
            f"✅ *Votre ticket a été résolu !*\n\n"
            f"🔖 Référence : `{ticket.ticket_ref}`\n"
            f"📂 Catégorie : {ticket.category}\n\n"
            "Merci d'avoir contacté notre support.\n"
            "Tapez *AIDE* si vous avez besoin d'aide supplémentaire."
        )
        self._send_whatsapp(ticket.client_whatsapp, msg)

    def _send_whatsapp(self, to: str, body: str):
        try:
            self.twilio_client.messages.create(body=body, from_=self.twilio_from, to=to)
            print(f"✅ Message envoyé à {to} - ticket_service.py:197")
        except Exception as e:
            print(f"❌ Erreur envoi WhatsApp à {to}: {e} - ticket_service.py:199")
