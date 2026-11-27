def test_import_hitl():
    import wincua_hitl as h

    assert h.__version__
    ui = h.LocalUI(port=8765)
    assert ui.url.endswith(":8765")
