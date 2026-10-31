import atexit
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="tracemind-tests-"))
os.environ.update(
    TRACEMIND_DATABASE_URL=f"sqlite:///{(_TMP / 'test.db').as_posix()}",
    TRACEMIND_STORAGE_LOCAL_DIR=str(_TMP / "uploads"),
    TRACEMIND_SEED_ON_STARTUP="false",
    TRACEMIND_DEMO_MODE="true",
    TRACEMIND_LLM_PROVIDER="deterministic",
    TRACEMIND_LOG_LEVEL="WARNING",
    TRACEMIND_SECRET_KEY="test-secret-key-that-is-long-enough-123",
)

from fastapi.testclient import TestClient  # noqa: E402

from app.bench.generator import FAULT_MESSAGES, Fault, simulate  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.llm.providers import set_provider_override  # noqa: E402
from app.main import app  # noqa: E402
from app.ratelimit import limiter  # noqa: E402
from app.services import jobs  # noqa: E402

jobs.set_inline(True)


@atexit.register
def _remove_tmp() -> None:
    engine.dispose()  # release the SQLite file first; Windows refuses to delete open files
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    limiter.reset()
    set_provider_override(None)
    yield
    set_provider_override(None)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def register(client: TestClient, email: str, password: str = "correct-horse-battery") -> TestClient:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password, "display_name": email.split("@")[0]})
    assert r.status_code == 201, r.text
    return client


@pytest.fixture
def make_user():
    clients = []

    def _make(email: str) -> TestClient:
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        return register(c, email)

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture(scope="session")
def small_scenario():
    """A 45-minute scenario with one bad deployment, generated once per test session."""
    start = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(hours=2)
    fault = Fault("payments-service", "bad_deployment", start + timedelta(minutes=30), start + timedelta(minutes=40), 0.9,
                  FAULT_MESSAGES["bad_deployment"][0])
    return simulate(seed=11, faults=[fault], start=start, minutes=45, requests_per_minute=24, scenario_id="t")


@pytest.fixture(scope="session")
def scenario_jsonl(small_scenario) -> bytes:
    return "\n".join(json.dumps(r) for r in small_scenario.records).encode()
