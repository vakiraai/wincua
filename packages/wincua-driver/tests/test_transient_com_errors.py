"""COM/UIA HRESULT classification for the Invoke retry (no pywinauto needed)."""

from __future__ import annotations

from wincua_driver.backend.windows import describe_com_error, transient_uia_error


class COMError(Exception):
    pass


def test_live_run_error_is_element_not_available():
    exc = COMError(-2147220991, "An event was unable to invoke any of the subscribers", (None, None, None, 0, None))
    assert transient_uia_error(exc) == "UIA_E_ELEMENTNOTAVAILABLE"
    assert describe_com_error(exc) == "UIA_E_ELEMENTNOTAVAILABLE 0x80040201"


def test_unsigned_hresult_attribute_and_chained_errors():
    exc = COMError("boom")
    exc.hresult = 0x80010108
    assert transient_uia_error(exc) == "RPC_E_DISCONNECTED"
    try:
        try:
            raise COMError(-2146233083, "timeout")
        except COMError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert transient_uia_error(outer) == "UIA_E_TIMEOUT"


def test_other_errors_are_not_transient():
    assert transient_uia_error(COMError(-2147024891, "access denied")) is None
    assert transient_uia_error(ValueError("nope")) is None
    assert describe_com_error(ValueError("nope")) == "ValueError: nope"
