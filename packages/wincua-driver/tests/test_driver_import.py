def test_import_driver():
    import wincua_driver as d

    assert d.__version__
    assert callable(d.observe)
    assert callable(d.act)


def test_null_backend_default():
    from wincua_driver.backend import get_backend, set_backend

    set_backend("null")
    b = get_backend()
    assert b.name == "null"
