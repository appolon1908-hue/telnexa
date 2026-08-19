from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import settings

kwargs = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=kwargs)
SessionLocal = sessionmaker(engine, expire_on_commit=False)
class Base(DeclarativeBase): pass

def session():
    db=SessionLocal()
    try: yield db
    finally: db.close()
