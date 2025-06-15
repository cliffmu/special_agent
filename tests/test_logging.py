import logging

from utils import logging as log


def test_info_log_level(hass, caplog):
    caplog.set_level(logging.INFO)
    log.info("Hello")
    assert "Hello" in caplog.text


def test_debug_toggle(hass, caplog):
    caplog.set_level(logging.DEBUG)
    log.debug("Verbose")
    assert "Verbose" in caplog.text
