from alembic.config import Config
from alembic.script import ScriptDirectory
from app.core.config import settings


def test_alembic_configuration_and_revisions():
    """Verify that Alembic configuration is coherent and revisions exist."""
    import os
    ini_path = "alembic.ini" if os.path.exists("alembic.ini") else "backend/alembic.ini"
    alembic_cfg = Config(ini_path)
    script_loc = "alembic" if os.path.exists("alembic") else "backend/alembic"
    alembic_cfg.set_main_option("script_location", script_loc)
    alembic_cfg.set_main_option("sqlalchemy.url", settings.migration_database_url)

    script_dir = ScriptDirectory.from_config(alembic_cfg)
    heads = script_dir.get_heads()

    assert len(heads) >= 1, "At least one Alembic migration revision head should exist"
    revisions = [rev.revision for rev in script_dir.walk_revisions()]
    assert "9d54944eede9" in revisions, "Phase 1 schema migration should be registered"
    assert "dfe6a65ca915" in revisions, "Phase 2 schema migration should be registered"
    assert "d1bbec0dcfae" in revisions, "Phase 3 schema migration should be registered"
    assert "e2cca71edb01" in revisions, "Phase 8 schema migration should be registered"
    assert "f3aa829c7102" in revisions, "Phase 9 schema migration should be registered"
    assert "b4de7a8912c3" in revisions, "Phase 10 schema migration should be registered"
    assert "c5e6f7a8b9c0" in revisions, "Phase 11 schema migration should be registered"
    assert "d6f7a8b9c0d1" in revisions, "Phase 12/13 schema migration should be registered"
    assert "e7f8a9b0c1d2" in revisions, "Phase 17 schema migration should be registered"
    assert "f8a9b0c1d2e3" in revisions, "Phase 18 schema migration should be registered"
    assert "a1b2c3d4e5f6" in revisions, "Subscriber schedule schema migration should be registered"
    assert "a1b2c3d4e5f6" in heads, "Subscriber schedule migration should be current head"
