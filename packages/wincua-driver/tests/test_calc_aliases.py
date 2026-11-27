"""Calculator AutomationId aliases (pure helpers)."""

from wincua_driver.backend.windows import _CALC_AUTO_ID_ALIASES, _CALC_NAME_FALLBACKS


def test_clear_button_aliases_include_clear_entry():
    assert "clearEntryButton" in _CALC_AUTO_ID_ALIASES["clearButton"]
    assert "clearButton" in _CALC_AUTO_ID_ALIASES["clearEntryButton"]


def test_digit_name_fallbacks_exist():
    assert "One" in _CALC_NAME_FALLBACKS["num1Button"]
    assert "Plus" in _CALC_NAME_FALLBACKS["plusButton"]
    assert "Equals" in _CALC_NAME_FALLBACKS["equalButton"]
