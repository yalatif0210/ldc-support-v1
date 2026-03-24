from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
from models import Ticket, Agent, TicketStatus, TicketPriority
import os

ESCALATION_CONFIG = {
    TicketPriority.HIGH:   1,
    TicketPriority.MEDIUM: 4,
    TicketPriority.LOW:    24,
}
MAX_REMINDERS = 3


class ReminderScheduler:
    def __init__(self, app, db, twilio_client):
        self.app = app
        self.db = db
        self.twilio_client = twilio_client
        self.twilio_from = os.getenv('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')
        self.manager_number = os.getenv('MANAGER_WHATSAPP_NUMBER', '')
        self.ticket_service = None  # Injecté après init pour éviter la circularité

        self.scheduler = BackgroundScheduler(timezone='UTC')
        self.scheduler.add_job(self._check_stale_tickets, IntervalTrigger(minutes=30), id='stale', replace_existing=True)
        self.scheduler.add_job(self._process_queue, IntervalTrigger(minutes=5), id='queue', replace_existing=True)

    def set_ticket_service(self, ts):
        self.ticket_service = ts

    def start(self):
        self.scheduler.start()
        print("✅ Scheduler démarré (relances + file d'attente).")

    def stop(self):
        self.scheduler.shutdown()

    def _check_stale_tickets(self):
        with self.app.app_context():
            now = datetime.utcnow()
            open_tickets = Ticket.query.filter(
                Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS])
            ).all()
            for ticket in open_tickets:
                delay_h = ESCALATION_CONFIG.get(ticket.priority, 4)
                if now >= ticket.created_at + timedelta(hours=delay_h):
                    hours_elapsed = int((now - ticket.created_at).total_seconds() / 3600)
                    self._send_reminder(ticket, hours_elapsed)

    def _process_queue(self):
        """Traite la file d'attente toutes les 5 minutes."""
        with self.app.app_context():
            if self.ticket_service:
                self.ticket_service.process_queue()

    def _send_reminder(self, ticket, hours_elapsed):
        count = ticket.reminder_count or 0
        if count >= MAX_REMINDERS:
            self._escalate_to_manager(ticket, hours_elapsed)
            return
        if ticket.agent:
            p_emoji = {'high':'🔴','medium':'🟡','low':'🟢'}.get(ticket.priority.value,'⚪')
            msg = (
                f"⏰ *RAPPEL — Ticket en attente*\n{'─'*28}\n"
                f"🔖 Réf : `{ticket.ticket_ref}`\n"
                f"👤 Client : {ticket.client_name}\n"
                f"{p_emoji} Priorité : {ticket.priority.value.upper()}\n{'─'*28}\n"
                f"⏱️ En attente depuis *{hours_elapsed}h*\n\n"
                f"Pour fermer : `FERMER {ticket.ticket_ref}`"
            )
            self._send_whatsapp(ticket.agent.whatsapp_number, msg)
            ticket.reminder_count = count + 1
            self.db.session.commit()
        else:
            self._escalate_to_manager(ticket, hours_elapsed)

    def _escalate_to_manager(self, ticket, hours_elapsed):
        if not self.manager_number:
            return
        msg = (
            f"🚨 *ESCALADE — Ticket non résolu*\n{'─'*28}\n"
            f"🔖 Réf : `{ticket.ticket_ref}`\n"
            f"👤 Client : {ticket.client_name}\n"
            f"🔴 Priorité : {ticket.priority.value.upper()}\n"
            f"👨‍💼 Agent : {ticket.agent.name if ticket.agent else '⚠️ NON ASSIGNÉ'}\n"
            f"{'─'*28}\n"
            f"⏱️ En attente depuis *{hours_elapsed}h* sans résolution."
        )
        self._send_whatsapp(self.manager_number, msg)

    def _send_whatsapp(self, to, body):
        try:
            self.twilio_client.messages.create(body=body, from_=self.twilio_from, to=to)
        except Exception as e:
            print(f"❌ Erreur relance WhatsApp: {e}")
