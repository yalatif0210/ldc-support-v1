from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
from models import Ticket, Agent, TicketStatus, TicketPriority
import httpx, os

ESCALATION_CONFIG = {
    TicketPriority.HIGH:   1,
    TicketPriority.MEDIUM: 4,
    TicketPriority.LOW:    24,
}
MAX_REMINDERS = 3


class ReminderScheduler:
    def __init__(self, app, db, ticket_service):
        self.app = app
        self.db = db
        self.ticket_service = ticket_service
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.manager_chat_id = os.getenv('MANAGER_TELEGRAM_CHAT_ID', '')
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

        self.scheduler = BackgroundScheduler(timezone='UTC')
        self.scheduler.add_job(self._check_stale_tickets, IntervalTrigger(minutes=30), id='stale', replace_existing=True)
        self.scheduler.add_job(self._process_queue, IntervalTrigger(minutes=5), id='queue', replace_existing=True)

    def start(self):
        self.scheduler.start()
        print("✅ Scheduler démarré. - scheduler.py:30")

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
        with self.app.app_context():
            self.ticket_service.process_queue()

    def _send_reminder(self, ticket, hours_elapsed):
        count = ticket.reminder_count or 0
        if count >= MAX_REMINDERS:
            self._escalate_to_manager(ticket, hours_elapsed)
            return
        if ticket.agent and ticket.agent.telegram_chat_id:
            p_emoji = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}.get(ticket.priority.value, '⚪')
            msg = (
                f"⏰ *RAPPEL — Ticket en attente*\n"
                f"🔖 Réf : `{ticket.ticket_ref}`\n"
                f"👤 Client : {ticket.client_name}\n"
                f"{p_emoji} Priorité : {ticket.priority.value.upper()}\n"
                f"⏱️ En attente depuis *{hours_elapsed}h*\n\n"
                f"Pour fermer : /fermer\\_{ticket.ticket_ref}"
            )
            self._send_telegram(ticket.agent.telegram_chat_id, msg)
            ticket.reminder_count = count + 1
            self.db.session.commit()
        else:
            self._escalate_to_manager(ticket, hours_elapsed)

    def _escalate_to_manager(self, ticket, hours_elapsed):
        if not self.manager_chat_id:
            return
        msg = (
            f"🚨 <b>ESCALADE — Ticket non résolu</b>\n"
            f"🔖 Réf : <code>{ticket.ticket_ref}</code>\n"
            f"👤 Client : {ticket.client_name}\n"
            f"🔴 Priorité : {ticket.priority.value.upper()}\n"
            f"👨‍💼 Agent : {ticket.agent.name if ticket.agent else '⚠️ NON ASSIGNÉ'}\n"
            f"⏱️ En attente depuis <b>{hours_elapsed}h</b>"
        )
        self._send_telegram(self.manager_chat_id, msg)

    def _send_telegram(self, chat_id, text):
        try:
            httpx.post(f"{self.api_url}/sendMessage",
                      json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
                      timeout=10)
        except Exception as e:
            print(f"❌ Erreur relance Telegram: {e} - scheduler.py:91")
