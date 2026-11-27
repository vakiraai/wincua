def test_import_wincua():
    import wincua

    assert wincua.__version__
    assert wincua.Agent
    assert wincua.Session


def test_provider_reexports():
    from wincua.providers import NullProvider, OllamaProvider, OpenAICompatProvider

    assert NullProvider().name == "null"
    assert OllamaProvider().name == "ollama"
    assert OpenAICompatProvider().name == "openai_compat"


def test_hitl_reexport():
    from wincua.hitl import LocalUI

    assert LocalUI(port=9).port == 9


def test_driver_reexport():
    from wincua.driver import Click, observe

    assert Click(element_id="e1").element_id == "e1"
    assert callable(observe)
