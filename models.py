from database import db
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import enum


class TicketStatus(enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


class TicketPriority(enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TicketCategory(enum.Enum):
    TECHNICAL = "Problème technique"
    BILLING = "Coaching"
    ACCOUNT = "Compte utilisateur"
    DELIVERY = "Rapport hebdomadaire"
    OTHER = "Autre"


class AgentRole(enum.Enum):
    AGENT = "agent"
    ADMIN = "admin"


class AgentStatus(enum.Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    ABSENT = "absent"


class Agent(db.Model):
    __tablename__ = 'agents'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    telegram_chat_id = db.Column(db.String(30), unique=True, nullable=True)
    role = db.Column(db.Enum(AgentRole), default=AgentRole.AGENT)
    is_active = db.Column(db.Boolean, default=True)
    status = db.Column(db.Enum(AgentStatus), default=AgentStatus.AVAILABLE)
    max_tickets = db.Column(db.Integer, default=5)
    current_ticket_count = db.Column(db.Integer, default=0)
    work_schedule = db.Column(db.JSON, default=lambda: {
        "mon": ["08:00", "18:00"], "tue": ["08:00", "18:00"],
        "wed": ["08:00", "18:00"], "thu": ["08:00", "18:00"],
        "fri": ["08:00", "18:00"], "sat": None, "sun": None,
    })
    timezone = db.Column(db.String(50), default="Africa/Abidjan")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    tickets = db.relationship('Ticket', backref='agent', lazy=True)

    @property
    def is_within_schedule(self) -> bool:
        import pytz
        now = datetime.now(pytz.timezone(self.timezone))
        day_map = {0:'mon',1:'tue',2:'wed',3:'thu',4:'fri',5:'sat',6:'sun'}
        hours = (self.work_schedule or {}).get(day_map[now.weekday()])
        if not hours:
            return False
        start_h, start_m = map(int, hours[0].split(':'))
        end_h, end_m = map(int, hours[1].split(':'))
        start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        return start <= now <= end

    @property
    def has_capacity(self) -> bool:
        return self.current_ticket_count < self.max_tickets

    @property
    def is_truly_available(self) -> bool:
        return (self.is_active and self.status == AgentStatus.AVAILABLE
                and self.is_within_schedule and self.has_capacity)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'email': self.email,
            'telegram_chat_id': self.telegram_chat_id,
            'role': self.role.value, 'is_active': self.is_active,
            'status': self.status.value, 'max_tickets': self.max_tickets,
            'current_ticket_count': self.current_ticket_count,
            'has_capacity': self.has_capacity,
            'is_within_schedule': self.is_within_schedule,
            'is_truly_available': self.is_truly_available,
            'work_schedule': self.work_schedule, 'timezone': self.timezone,
            'created_at': self.created_at.isoformat()
        }


class Ticket(db.Model):
    __tablename__ = 'tickets'

    id = db.Column(db.Integer, primary_key=True)
    ticket_ref = db.Column(db.String(20), unique=True, nullable=False)
    client_name = db.Column(db.String(100), nullable=False)
    client_whatsapp = db.Column(db.String(30), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=False)
    priority = db.Column(db.Enum(TicketPriority), default=TicketPriority.MEDIUM)
    status = db.Column(db.Enum(TicketStatus), default=TicketStatus.OPEN)
    agent_id = db.Column(db.Integer, db.ForeignKey('agents.id'), nullable=True)
    reminder_count = db.Column(db.Integer, default=0)
    queued = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id, 'ticket_ref': self.ticket_ref,
            'client_name': self.client_name, 'client_whatsapp': self.client_whatsapp,
            'category': self.category, 'description': self.description,
            'priority': self.priority.value, 'status': self.status.value,
            'agent': self.agent.to_dict() if self.agent else None,
            'reminder_count': self.reminder_count, 'queued': self.queued,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'closed_at': self.closed_at.isoformat() if self.closed_at else None
        }


class Conversation(db.Model):
    __tablename__ = 'conversations'

    id = db.Column(db.Integer, primary_key=True)
    client_whatsapp = db.Column(db.String(30), nullable=False, index=True)
    step = db.Column(db.String(30), default='ask_name')
    temp_data = db.Column(db.JSON, default={})
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)