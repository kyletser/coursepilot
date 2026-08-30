from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def required_database_revision() -> str:
    api_root = Path(__file__).resolve().parents[1]
    config = Config(str(api_root / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError("CoursePilot requires a single Alembic head")
    return heads[0]
