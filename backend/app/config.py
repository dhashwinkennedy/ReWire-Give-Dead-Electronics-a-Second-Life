from pathlib import Path
import os

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - the application still supports shell-provided env vars
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[1]
if load_dotenv is not None:
    # Keep configuration local to this application and never overwrite deployment values.
    load_dotenv(ROOT / ".env", override=False)
else:
    # The API only needs simple KEY=value settings.  Keep local development usable
    # before optional dependencies have been installed, while preserving deployment
    # environment variables as the source of precedence.
    env_file = ROOT / ".env"
    if env_file.is_file():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip("\"'")
            if key:
                os.environ.setdefault(key, value)
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"
LOG_DIR = ROOT / "logs"
CATALOG_STRICT = os.getenv("CATALOG_STRICT", "true").strip().lower() not in {"0", "false", "no"}
CATALOG_VERSION_FALLBACK = "2026.10.05"


def get_ai_search_settings() -> tuple[str | None, str | None]:
    """Read AI settings at request time so configuration changes do not require imports."""
    model = os.getenv("AI_MODEL", "").strip() or None
    tavily_api_key = os.getenv("TAVILY_API_KEY", "").strip() or None
    return model, tavily_api_key
