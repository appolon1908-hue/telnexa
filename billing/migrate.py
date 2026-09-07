from pathlib import Path
from sqlalchemy import text
from .db import Base, engine
from . import models  # noqa: F401 - registers complete metadata before create_all
from . import sms_integration  # noqa: F401 - transactional acceptance receipts

Base.metadata.create_all(engine)
if engine.dialect.name == "postgresql":
    with engine.begin() as db:
        migration_dir = Path(__file__).resolve().parent / "migrations"
        for migration in sorted(migration_dir.glob("*.sql")):
            db.execute(text(migration.read_text()))
print("billing schema current")
