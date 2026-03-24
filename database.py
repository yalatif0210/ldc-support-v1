from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def init_db():
    from models import Agent, Ticket, Conversation, AgentRole
    db.create_all()

    if Agent.query.count() == 0:
        agents_data = [
            dict(name="Admin Système",  email="admin@support.com",  role=AgentRole.ADMIN,  password="Admin1234!"),
            dict(name="Alice Dupont",   email="alice@support.com",  role=AgentRole.AGENT,  password="Alice1234!"),
            dict(name="Bob Martin",     email="bob@support.com",    role=AgentRole.AGENT,  password="Bob1234!"),
            dict(name="Claire Ndiaye",  email="claire@support.com", role=AgentRole.AGENT,  password="Claire1234!"),
        ]
        for d in agents_data:
            a = Agent(name=d['name'], email=d['email'], role=d['role'], is_active=True)
            a.set_password(d['password'])
            db.session.add(a)
        db.session.commit()
        print("✅ Comptes de démonstration créés.")
        print("   admin@support.com  /  Admin1234!")
        print("   alice@support.com  /  Alice1234!")

    print("✅ Base de données initialisée.")
