import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DMX_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ.get(
    "DMX_DATABASE_URL",
    f"sqlite:///{DATA_DIR / 'dmx.db'}",
)

DMX_PASSWORD = os.environ.get("DMX_PASSWORD", "secretsauce")

# Secret key used to sign session cookies. Persist one to disk so sessions
# survive restarts without configuring an env var.
_SECRET_PATH = DATA_DIR / "session.key"
if _SECRET_PATH.exists():
    SESSION_SECRET = _SECRET_PATH.read_text().strip()
else:
    SESSION_SECRET = os.urandom(32).hex()
    _SECRET_PATH.write_text(SESSION_SECRET)
    try:
        os.chmod(_SECRET_PATH, 0o600)
    except OSError:
        pass

SESSION_COOKIE_NAME = "dmx_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days

FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"
