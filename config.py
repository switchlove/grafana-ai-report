import os
import pathlib

# Load .env file (no extra dependencies required)
_env_file = pathlib.Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"Error: required environment variable {name!r} is not set. Add it to .env or export it."
        )
    return value


GRAFANA_URL      = require_env("GRAFANA_URL")
REPORT_DIR       = pathlib.Path(require_env("REPORT_DIR"))
GRAFANA_TOKEN    = require_env("GRAFANA_TOKEN")
OPENAI_API_KEY   = require_env("OPENAI_API_KEY")
DATA_SOURCE_UID  = require_env("DATA_SOURCE_UID")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
