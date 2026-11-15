import pytest

from toward import safety


@pytest.fixture(autouse=True)
def no_live_safety_agent():
    """Unit tests never call Groq. The live Safety agent is tested separately in tests/redteam."""
    before = safety.current()
    safety.install(None)
    yield
    safety.install(before)
