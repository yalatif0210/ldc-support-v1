from flask import Flask, request, jsonify
from flask_cors import CORS
from functools import wraps
import jwt, os, datetime as dt, httpx
from database import db, init_db
from models import Ticket, Agent, AgentStatus, Conversation, TicketStatus, TicketPriority, AgentRole
from ticket_service import TicketService
from bot_handler import BotHandler
from scheduler import ReminderScheduler

app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'postgresql://support_user:support_pass@localhost:5432/support_db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET'] = os.getenv('JWT_SECRET', 'jwt-secret-change-in-prod')
app.config['JWT_EXPIRY_HOURS'] = 8

db.init_app(app)

# Init DB au démarrage (compatible Gunicorn)
with app.app_context():
    init_db()

ticket_service = TicketService(db)
bot_handler = BotHandler(db, ticket_service)
reminder_scheduler = ReminderScheduler(app, db, ticket_service)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')


# ── JWT ───────────────────────────────────────────────────────────────────────

def make_token(agent):
    payload = {'agent_id': agent.id, 'role': agent.role.value,
               'exp': dt.datetime.utcnow() + dt.timedelta(hours=app.config['JWT_EXPIRY_HOURS'])}
    return jwt.encode(payload, app.config['JWT_SECRET'], algorithm='HS256')

def decode_token(token):
    return jwt.decode(token, app.config['JWT_SECRET'], algorithms=['HS256'])

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'error': 'Token manquant'}), 401
        try:
            payload = decode_token(auth.split(' ')[1])
            request.agent_id = payload['agent_id']
            request.agent_role = payload['role']
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Session expirée'}), 401
        except Exception:
            return jsonify({'error': 'Token invalide'}), 401
        return f(*args, **kwargs)
    return decorated

def require_admin(f):
    @wraps(f)
    @require_auth
    def decorated(*args, **kwargs):
        if request.agent_role != AgentRole.ADMIN.value:
            return jsonify({'error': 'Accès admin requis'}), 403
        return f(*args, **kwargs)
    return decorated


# ── Telegram Webhook ──────────────────────────────────────────────────────────

@app.route(f'/webhook/telegram/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    """Reçoit les mises à jour Telegram."""
    data = request.json or {}
    message = data.get('message', {})

    if not message:
        return jsonify({'ok': True})

    chat_id = str(message.get('chat', {}).get('id', ''))
    text = message.get('text', '').strip()
    user = message.get('from', {})
    user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()

    if not chat_id or not text:
        return jsonify({'ok': True})

    reply = bot_handler.handle_message(chat_id, text, user_name)

    # Envoyer la réponse via API Telegram
    try:
        httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={'chat_id': chat_id, 'text': reply, 'parse_mode': 'HTML'},
            timeout=10
        )
    except Exception as e:
        print(f"❌ Erreur envoi réponse Telegram: {e} - app.py:97")

    return jsonify({'ok': True})


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json or {}
    agent = Agent.query.filter_by(email=data.get('email','').strip().lower(), is_active=True).first()
    if not agent or not agent.check_password(data.get('password','')):
        return jsonify({'error': 'Email ou mot de passe incorrect'}), 401
    return jsonify({'token': make_token(agent), 'agent': agent.to_dict()})

@app.route('/api/auth/me', methods=['GET'])
@require_auth
def me():
    return jsonify(Agent.query.get(request.agent_id).to_dict())


# ── Agent status ──────────────────────────────────────────────────────────────

@app.route('/api/agents/me/status', methods=['PUT'])
@require_auth
def update_my_status():
    data = request.json or {}
    try:
        new_status = AgentStatus(data['status'])
    except (KeyError, ValueError):
        return jsonify({'error': 'Statut invalide'}), 400
    agent = Agent.query.get(request.agent_id)
    ticket_service.set_agent_status(agent, new_status)
    return jsonify({'message': f'Statut mis à jour', 'agent': agent.to_dict()})

@app.route('/api/agents/<int:agent_id>/status', methods=['PUT'])
@require_admin
def update_agent_status(agent_id):
    data = request.json or {}
    try:
        new_status = AgentStatus(data['status'])
    except (KeyError, ValueError):
        return jsonify({'error': 'Statut invalide'}), 400
    agent = Agent.query.get_or_404(agent_id)
    ticket_service.set_agent_status(agent, new_status)
    return jsonify({'message': 'Statut mis à jour', 'agent': agent.to_dict()})

@app.route('/api/agents/<int:agent_id>/schedule', methods=['PUT'])
@require_admin
def update_agent_schedule(agent_id):
    agent = Agent.query.get_or_404(agent_id)
    data = request.json or {}
    if 'work_schedule' in data:
        agent.work_schedule = data['work_schedule']
    if 'max_tickets' in data:
        agent.max_tickets = max(1, int(data['max_tickets']))
    if 'timezone' in data:
        agent.timezone = data['timezone']
    if 'telegram_chat_id' in data:
        agent.telegram_chat_id = data['telegram_chat_id']
    db.session.commit()
    return jsonify({'message': 'Configuration mise à jour', 'agent': agent.to_dict()})


# ── Tickets ───────────────────────────────────────────────────────────────────

@app.route('/api/tickets', methods=['GET'])
@require_auth
def get_tickets():
    query = Ticket.query
    if request.agent_role == AgentRole.AGENT.value:
        query = query.filter_by(agent_id=request.agent_id)
    return jsonify([t.to_dict() for t in query.order_by(Ticket.created_at.desc()).all()])

@app.route('/api/tickets/queue', methods=['GET'])
@require_admin
def get_queue():
    queued = (Ticket.query.filter_by(queued=True)
              .filter(Ticket.status == TicketStatus.OPEN)
              .order_by(Ticket.priority.desc(), Ticket.created_at.asc()).all())
    return jsonify([t.to_dict() for t in queued])

@app.route('/api/tickets/<int:ticket_id>/start', methods=['PUT'])
@require_auth
def start_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if request.agent_role == AgentRole.AGENT.value and ticket.agent_id != request.agent_id:
        return jsonify({'error': 'Accès refusé'}), 403
    if ticket.agent_id is None:
        agent = Agent.query.get(request.agent_id)
        if agent:
            ticket.agent_id = agent.id
            agent.current_ticket_count += 1
    ticket_service.start_ticket(ticket)
    return jsonify({'message': 'Prise en charge confirmée', 'ticket': ticket.to_dict()})

@app.route('/api/tickets/<int:ticket_id>/close', methods=['PUT'])
@require_auth
def close_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if request.agent_role == AgentRole.AGENT.value and ticket.agent_id != request.agent_id:
        return jsonify({'error': 'Accès refusé'}), 403
    if ticket.status == TicketStatus.CLOSED:
        return jsonify({'error': 'Ticket déjà fermé'}), 400
    ticket_service.close_ticket(ticket)
    return jsonify({'message': 'Ticket fermé', 'ticket': ticket.to_dict()})

@app.route('/api/stats', methods=['GET'])
@require_auth
def get_stats():
    is_admin = request.agent_role == AgentRole.ADMIN.value
    base = Ticket.query if is_admin else Ticket.query.filter_by(agent_id=request.agent_id)
    queued_count = Ticket.query.filter_by(queued=True).filter(Ticket.status == TicketStatus.OPEN).count() if is_admin else 0
    return jsonify({
        'total': base.count(),
        'open': base.filter_by(status=TicketStatus.OPEN).count(),
        'in_progress': base.filter_by(status=TicketStatus.IN_PROGRESS).count(),
        'closed': base.filter_by(status=TicketStatus.CLOSED).count(),
        'high_priority_open': base.filter_by(priority=TicketPriority.HIGH).filter(Ticket.status != TicketStatus.CLOSED).count(),
        'queued': queued_count,
        'agents_active': Agent.query.filter_by(is_active=True).count() if is_admin else None,
        'agents_available': len([a for a in Agent.query.filter_by(is_active=True).all() if a.is_truly_available]) if is_admin else None,
    })


# ── Agents ────────────────────────────────────────────────────────────────────

@app.route('/api/agents', methods=['GET'])
@require_admin
def get_agents():
    return jsonify([a.to_dict() for a in Agent.query.filter_by(is_active=True).all()])

@app.route('/api/agents', methods=['POST'])
@require_admin
def create_agent():
    data = request.json
    if Agent.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'Email déjà utilisé'}), 400
    agent = Agent(
        name=data['name'], email=data['email'],
        telegram_chat_id=data.get('telegram_chat_id'),
        role=AgentRole(data.get('role', 'agent')),
        max_tickets=data.get('max_tickets', 5), is_active=True
    )
    agent.set_password(data['password'])
    db.session.add(agent)
    db.session.commit()
    return jsonify(agent.to_dict()), 201

@app.route('/api/agents/<int:agent_id>/password', methods=['PUT'])
@require_auth
def change_password(agent_id):
    if request.agent_role != AgentRole.ADMIN.value and request.agent_id != agent_id:
        return jsonify({'error': 'Accès refusé'}), 403
    agent = Agent.query.get_or_404(agent_id)
    agent.set_password(request.json['new_password'])
    db.session.commit()
    return jsonify({'message': 'Mot de passe mis à jour'})


@app.route('/api/qr-info', methods=['GET'])
@require_auth
def qr_info():
    bot_username = os.getenv('TELEGRAM_BOT_USERNAME', '')
    url = f"https://t.me/{bot_username}" if bot_username else ''
    return jsonify({'qr_data': url, 'bot_username': bot_username})


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route('/dashboard/')
@app.route('/dashboard')
def dashboard():
    from flask import send_from_directory
    return send_from_directory('/app/dashboard', 'index.html')


# ── Setup webhook Telegram ────────────────────────────────────────────────────

@app.route('/api/setup-webhook', methods=['POST'])
@require_admin
def setup_webhook():
    """Configure le webhook Telegram automatiquement."""
    base_url = request.json.get('base_url', '')
    if not base_url:
        return jsonify({'error': 'base_url requis'}), 400
    webhook_url = f"{base_url}/webhook/telegram/{TELEGRAM_BOT_TOKEN}"
    resp = httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
        json={'url': webhook_url}
    )
    return jsonify(resp.json())


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    reminder_scheduler.start()
    import atexit
    atexit.register(reminder_scheduler.stop)
    app.run(debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true', port=5000)