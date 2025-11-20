# db.py
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, scoped_session, Session

# .env (если есть)
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


def _strip_invisibles(s: str) -> str:
    # защита от невидимых символов (которые иногда попадают из копипаста)
    return "".join(c for c in s if c.isprintable())


raw_url = os.getenv("DATABASE_URL", "")
if raw_url:
    db_url = _strip_invisibles(raw_url)
    if any(c in db_url for c in ["“", "”", "«", "»", "„"]):
        raise RuntimeError(
            "DATABASE_URL contains smart quotes. Replace with standard ' or \" quotes."
        )
else:
    # fallback (локальный SQLite)
    db_url = "sqlite:///invoice_app.sqlite3"

engine = create_engine(db_url, echo=False, future=True)


class Base(DeclarativeBase):
    pass


SessionLocal: sessionmaker[Session] = scoped_session(
    sessionmaker(bind=engine, autoflush=False, autocommit=False)
)
