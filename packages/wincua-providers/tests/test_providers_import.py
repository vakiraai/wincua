def test_import_providers():
    import wincua_providers as p

    assert p.__version__
    assert p.NullProvider().name == "null"
    assert p.OllamaProvider().name == "ollama"
    assert p.OpenAICompatProvider().name == "openai_compat"
