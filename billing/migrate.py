from pathlib import Path

from sqlalchemy import text

from .db import Base, engine
from .sms_provider import models as _sms_provider_models  # noqa: F401

Base.metadata.create_all(engine)
if engine.dialect.name == "postgresql":
    with engine.begin() as db:
        for migration in sorted(Path("/app/billing/migrations").glob("*.sql")):
            db.execute(text(migration.read_text()))
print("billing schema current")
