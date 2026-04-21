from __future__ import annotations

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

# Per-LightModel reference images are stored here as <model_id>.webp.
MODEL_IMAGES_DIR = Path(
    os.environ.get("DMX_MODEL_IMAGES_DIR", DATA_DIR / "model_images")
)
MODEL_IMAGES_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Anthropic (Claude) configuration
# ---------------------------------------------------------------------------
def _load_anthropic_api_key() -> str:
    """Prefer the env var; fall back to parsing ``claudeKey.env`` at repo root.

    The fallback exists so local dev works without sourcing the file. In
    production the systemd unit uses ``EnvironmentFile=`` and this helper
    never reaches the file read path."""
    env_val = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env_val:
        return env_val
    candidates = [
        BASE_DIR.parent / "claudeKey.env",
        BASE_DIR / "claudeKey.env",
    ]
    for path in candidates:
        try:
            content = path.read_text().strip()
        except OSError:
            continue
        if not content:
            continue
        # Accept both ``KEY=VALUE`` and a bare token.
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
            if line.startswith("sk-"):
                return line
        # Fallback: first non-comment line treated as bare token.
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return ""


ANTHROPIC_API_KEY = _load_anthropic_api_key()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-7").strip()
