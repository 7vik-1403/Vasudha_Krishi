import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
SQLITE_PATH = os.path.join(INSTANCE_DIR, "app.db").replace("\\", "/")

# Load .env before the Config class reads the environment, so values set here
# are picked up regardless of which module imports Config first.
load_dotenv(os.path.join(BASE_DIR, ".env"))

# The built-in fallback used only for local development / tests. create_app()
# logs a warning whenever this value is still in effect.
DEFAULT_SECRET_KEY = "crop-recommendation-dev-key-change-me"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY)
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or f"sqlite:///{SQLITE_PATH}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_TIME_LIMIT = None
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    GEMINI_FALLBACK_MODELS = os.environ.get("GEMINI_FALLBACK_MODELS", "")
    PREDICT_CACHE_HOURS = int(os.environ.get("PREDICT_CACHE_HOURS", "72"))
    AI_RATE_LIMIT_PER_MINUTE = int(os.environ.get("AI_RATE_LIMIT_PER_MINUTE", "20"))
    DISEASE_DETECTION_ENABLED = os.environ.get("DISEASE_DETECTION_ENABLED", "1").lower() not in ("0", "false", "no")
    DISEASE_MAX_IMAGE_BYTES = int(os.environ.get("DISEASE_MAX_IMAGE_BYTES", str(5 * 1024 * 1024)))
    DEMO_MODE_ENABLED = os.environ.get("DEMO_MODE_ENABLED", "1").lower() not in ("0", "false", "no")
    SEED_DEMO_ON_START = os.environ.get("SEED_DEMO_ON_START", "0").lower() in ("1", "true", "yes")
