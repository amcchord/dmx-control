"""Always initialize a fresh test database and disable real lighting output."""
import os
import tempfile

# Set these before test modules import the application and bind its DB engine.
_test_data = tempfile.TemporaryDirectory(prefix='dmx-tests-')
os.environ.setdefault('DMX_DATA_DIR', _test_data.name)
os.environ['DMX_OUTPUT_ENABLED'] = 'false'


def pytest_sessionstart(session):
    from app.db import init_db
    from app.seed import seed
    init_db()
    seed()


def pytest_sessionfinish(session, exitstatus):
    _test_data.cleanup()
