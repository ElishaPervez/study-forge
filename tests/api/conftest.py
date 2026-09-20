import pytest

from tests.api.helpers import close_tracked_clients


@pytest.fixture(autouse=True)
def _close_created_apps():
    """Stop the background worker of every app a test created."""
    yield
    close_tracked_clients()
