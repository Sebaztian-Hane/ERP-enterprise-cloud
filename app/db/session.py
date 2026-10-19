from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from app.core.config import get_settings
from app.services.rag_sync import clear_after_rollback, dispatch_after_commit

settings = get_settings()
database_url = settings.database_url
# Supabase muestra URLs con postgresql://; SQLAlchemy necesita el driver
# explícito para usar psycopg v3, que es el driver incluido por este proyecto.
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
event.listen(SessionLocal, "after_commit", dispatch_after_commit)
event.listen(SessionLocal, "after_rollback", clear_after_rollback)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
