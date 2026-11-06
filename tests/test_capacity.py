from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from shared import capacity
from shared.redis_bus import RetryLater


@pytest.mark.asyncio
@pytest.mark.parametrize("free_gb,db_gb,reason", [(9, 1, "disk_free"), (80, 8, "database_budget")])
async def test_capacity_pauses_without_deleting_data(monkeypatch, free_gb, db_gb, reason):
    monkeypatch.setattr(capacity.shutil, "disk_usage", lambda path: SimpleNamespace(free=free_gb * 1024 ** 3))
    monkeypatch.setattr(capacity, "get_settings", lambda: SimpleNamespace(
        storage_data_path=".", storage_min_free_gb=10, storage_max_database_gb=8))
    session = SimpleNamespace(scalar=AsyncMock(return_value=db_gb * 1024 ** 3))
    with pytest.raises(RetryLater, match=reason):
        await capacity.ensure_capacity(session)
    assert str(session.scalar.call_args.args[0]) == "SELECT pg_database_size(current_database())"
