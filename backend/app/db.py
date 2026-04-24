from collections.abc import Iterator

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from .config import DATABASE_URL

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, connect_args=_connect_args)


def init_db() -> None:
    # Import models so SQLModel knows about them before create_all.
    from . import models  # noqa: F401

    # Rename the legacy `scene` table to `effect` BEFORE create_all so
    # SQLModel doesn't create an empty second table alongside the one we
    # want to keep (see _rename_scene_to_effect). Idempotent.
    _rename_scene_to_effect()
    SQLModel.metadata.create_all(engine)
    _migrate()


def _rename_scene_to_effect() -> None:
    """Rename the pre-existing ``scene`` table to ``effect`` if needed.

    Historical name: the animated effect presets lived in a table called
    ``scene``. That concept was renamed to ``Effect`` in code; rename the
    table to match so existing DBs keep their data instead of quietly
    starting over with an empty ``effect`` table. Safe to re-run."""
    is_sqlite = DATABASE_URL.startswith("sqlite")
    with engine.begin() as conn:
        if is_sqlite:
            rows = conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('scene', 'effect')"
            ).fetchall()
            tables = {r[0] for r in rows}
        else:
            try:
                rows = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_name IN ('scene', 'effect')"
                    )
                ).fetchall()
                tables = {r[0] for r in rows}
            except Exception:
                tables = set()
        if "scene" in tables and "effect" not in tables:
            conn.exec_driver_sql("ALTER TABLE scene RENAME TO effect")


def _table_columns(conn, table: str) -> set[str]:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _migrate() -> None:
    """Idempotent migrations for pre-existing SQLite databases.

    SQLModel.metadata.create_all() creates *tables* that don't exist yet but
    does not add *columns* to pre-existing tables. Keep everything additive
    and safe to re-run."""
    from sqlmodel import select

    from .models import Light, LightModel, LightModelMode

    is_sqlite = DATABASE_URL.startswith("sqlite")

    with engine.begin() as conn:
        if is_sqlite:
            # lightmodel.image_filename (new per-model image column)
            cols = _table_columns(conn, "lightmodel")
            if "image_filename" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE lightmodel ADD COLUMN image_filename TEXT"
                )
            # light.mode_id (new per-light mode selector)
            cols = _table_columns(conn, "light")
            if "mode_id" not in cols:
                conn.exec_driver_sql("ALTER TABLE light ADD COLUMN mode_id INTEGER")
            # light.zone_state / light.motion_state (compound fixtures)
            if "zone_state" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE light ADD COLUMN zone_state JSON"
                )
            if "motion_state" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE light ADD COLUMN motion_state JSON"
                )
            # light.notes (designer AI context)
            if "notes" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE light ADD COLUMN notes TEXT"
                )
            # controller.notes (designer AI context)
            ctrl_cols = _table_columns(conn, "controller")
            if "notes" not in ctrl_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE controller ADD COLUMN notes TEXT"
                )
            # lightmodelmode.layout (zone/motion overlay for a DMX mode)
            mode_cols = _table_columns(conn, "lightmodelmode")
            if "layout" not in mode_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE lightmodelmode ADD COLUMN layout JSON"
                )
        else:
            # Best-effort for other backends; swallow errors if column already exists.
            for stmt in (
                "ALTER TABLE lightmodel ADD COLUMN image_filename VARCHAR",
                "ALTER TABLE light ADD COLUMN mode_id INTEGER",
                "ALTER TABLE light ADD COLUMN zone_state JSON",
                "ALTER TABLE light ADD COLUMN motion_state JSON",
                "ALTER TABLE light ADD COLUMN notes TEXT",
                "ALTER TABLE controller ADD COLUMN notes TEXT",
                "ALTER TABLE lightmodelmode ADD COLUMN layout JSON",
            ):
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass

    # Synthesize a default LightModelMode for every LightModel that has none,
    # then backfill Light.mode_id to point at the model's default.
    with Session(engine) as sess:
        models = sess.exec(select(LightModel)).all()
        for m in models:
            existing = sess.exec(
                select(LightModelMode).where(LightModelMode.model_id == m.id)
            ).first()
            if existing is not None:
                continue
            mode_name = f"{m.channel_count}ch" if m.channel_count else "Default"
            sess.add(
                LightModelMode(
                    model_id=m.id,
                    name=mode_name,
                    channels=list(m.channels or []),
                    channel_count=m.channel_count or 0,
                    is_default=True,
                )
            )
        sess.commit()

        unmapped = sess.exec(select(Light).where(Light.mode_id.is_(None))).all()
        if unmapped:
            default_by_model: dict[int, int] = {}
            for light in unmapped:
                mid = default_by_model.get(light.model_id)
                if mid is None:
                    default = sess.exec(
                        select(LightModelMode).where(
                            LightModelMode.model_id == light.model_id,
                            LightModelMode.is_default == True,  # noqa: E712
                        )
                    ).first()
                    if default is None:
                        default = sess.exec(
                            select(LightModelMode).where(
                                LightModelMode.model_id == light.model_id
                            )
                        ).first()
                    if default is not None:
                        default_by_model[light.model_id] = default.id
                        mid = default.id
                if mid is not None:
                    light.mode_id = mid
                    sess.add(light)
            sess.commit()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
