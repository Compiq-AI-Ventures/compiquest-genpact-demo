from alembic.config import Config
from app.core.config import get_settings

cfg = Config("alembic.ini")
cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
section = cfg.get_section(cfg.config_ini_section, {}) or {}
print("alembic section:", section)
