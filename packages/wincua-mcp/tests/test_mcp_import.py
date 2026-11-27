def test_import_mcp():
    import wincua_mcp as m

    assert m.__version__
    assert set(m.TOOL_NAMES) == {"observe", "act", "launch", "list_windows", "ask_human"}


def test_package_import_does_not_pull_in_sdk():
    import os
    import subprocess
    import sys

    code = "import sys, wincua_mcp; assert 'mcp' not in sys.modules"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    subprocess.run([sys.executable, "-c", code], check=True, env=env)
