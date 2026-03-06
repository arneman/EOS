from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from akkudoktoreos.core.ems import EnergyManagement
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.utils.datetimeutil import to_datetime


@pytest.mark.asyncio
async def test_run_respect_interval_executes_when_interval_elapsed(monkeypatch, config_eos):
    config_eos.ems.interval = 300
    EnergyManagement._last_run_datetime = to_datetime().subtract(seconds=301)

    run_in_executor_mock = AsyncMock()
    fake_loop = SimpleNamespace(run_in_executor=run_in_executor_mock)
    monkeypatch.setattr("akkudoktoreos.core.ems.get_running_loop", lambda: fake_loop)

    await EnergyManagement().run(mode=EnergyManagementMode.PREDICTION, respect_interval=True)

    run_in_executor_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_respect_interval_skips_when_interval_not_elapsed(monkeypatch, config_eos):
    config_eos.ems.interval = 300
    EnergyManagement._last_run_datetime = to_datetime().subtract(seconds=30)

    run_in_executor_mock = AsyncMock()
    fake_loop = SimpleNamespace(run_in_executor=run_in_executor_mock)
    monkeypatch.setattr("akkudoktoreos.core.ems.get_running_loop", lambda: fake_loop)

    await EnergyManagement().run(mode=EnergyManagementMode.PREDICTION, respect_interval=True)

    run_in_executor_mock.assert_not_awaited()
