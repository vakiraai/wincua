"""Monorepo smoke: all Phase-1 packages import without Cua or Windows desktop."""


def test_import_all_core_packages():
    import wincua
    import wincua_driver
    import wincua_hitl
    import wincua_mcp
    import wincua_providers

    assert wincua.__version__ == "0.1.0"
    assert wincua_driver.__version__ == "0.1.0"
    assert wincua_providers.__version__ == "0.1.0"
    assert wincua_hitl.__version__ == "0.1.0"
    assert wincua_mcp.__version__ == "0.1.0"


def test_mock_backend_observe_act():
    import asyncio

    from wincua_driver import Click, act, observe
    from wincua_driver.backend import set_backend

    set_backend("mock")

    async def _run():
        obs = await observe(target="Calculator", include=["uia", "app"])
        assert obs.uia
        assert obs.app is not None
        result = await act(Click(element_id="e2"))
        assert result.ok
        assert result.mode_used == "mock"

    asyncio.run(_run())


def test_agent_stub_run():
    import asyncio

    from wincua import Agent
    from wincua.providers import NullProvider

    async def _run():
        agent = Agent(task="noop", provider=NullProvider(), allow=[])
        result = await agent.run()
        assert result.ok is False  # stub
        assert result.steps

    asyncio.run(_run())


def test_optional_pack_stubs_import():
    import wincua_excel
    import wincua_slicer

    assert wincua_excel.__version__
    assert wincua_slicer.__version__
