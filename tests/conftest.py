import pytest

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests that start real subprocesses (~20s each)"
    )
