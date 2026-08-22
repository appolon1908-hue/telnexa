from pathlib import Path
from sqlalchemy import text
from .db import Base, engine

Base.metadata.create_all(engine)
if engine.dialect.name == "postgresql":
    with engine.begin() as db:
        for migration in sorted(Path("/app/billing/migrations").glob("*.sql")):
            db.execute(text(migration.read_text()))
print("billing schema current")
