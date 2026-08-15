from pathlib import Path
from sqlalchemy import text
from .db import Base,engine
from . import models  # register all metadata before create_all
Base.metadata.create_all(engine)
if engine.dialect.name=="postgresql":
    with engine.begin() as db: db.execute(text(Path("/app/billing/migrations/001_billing_foundation.sql").read_text()))
print("billing schema current")
