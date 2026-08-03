"""SQLAlchemy declarative base shared by all ORM models.

All model classes must inherit from Base so that:
- Alembic can discover tables via Base.metadata
- Schema creation in tests works with Base.metadata.create_all()
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
