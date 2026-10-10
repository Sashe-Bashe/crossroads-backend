from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from market import manage
from market.config import settings
from market.db import Base, engine, session
from market.main import app


@pytest.fixture
def system(monkeypatch):
    # A private schema per test. Never truncate, reset, or modify the application's demo data.
    schema = "test_" + uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    test_engine = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(test_engine)
    factory = sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr(manage, "SessionLocal", factory)
    monkeypatch.setattr(settings, "demo_enabled", True)

    def dependency():
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    app.dependency_overrides[session] = dependency
    manage.seed(images=False)
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, factory
    app.dependency_overrides.clear()
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.fixture
def client(system):
    return system[0]
