"""Pytest configuration and shared fixtures."""

import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def disable_signal_state_persistence(tmp_path):
    """
    Isolate every test from on-disk signal_state.json.

    SharedState now persists signal counters/history to a JSON file so they
    survive process restarts. Without isolation each fresh SharedState() would
    load state left behind by a previous test (or a real bot run), breaking
    assertions like `assert state.signal_seq == 1`.

    This autouse fixture patches STATE_FILE to a unique temp path per test so
    each test starts from a clean slate, and writes are harmlessly discarded
    when the tmp_path is cleaned up.
    """
    with patch('bot.state.STATE_FILE', str(tmp_path / 'signal_state.json')):
        yield
