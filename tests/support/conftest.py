"""tests/support 公共夹具：独立文件库（swap_engine seam）。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from services.db.models import Base


@pytest.fixture()
async def support_db(tmp_path):
    """独立文件库 + swap_engine（T109 公开 seam），建全量表。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'support_test.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module.swap_engine(engine, factory)
    yield factory
    await engine.dispose()
