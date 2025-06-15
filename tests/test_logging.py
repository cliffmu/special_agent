import logging

import utils.logging as log


def test_info_log_level(hass, caplog):
    caplog.set_level(logging.INFO)
    log.info("Hello")
    assert "Hello" in caplog.text


def test_debug_toggle(hass, caplog):
    caplog.set_level(logging.DEBUG)
    log.debug("Verbose")
    assert "Verbose" in caplog.text


def test_debug_handles_format_errors(hass, caplog):
    caplog.set_level(logging.DEBUG)
    log.debug("Missing placeholder:", {"foo": 1})
    assert "Missing placeholder:" in caplog.text
    assert "'foo': 1" in caplog.text


def test_debug_wrong_arg_count(hass, caplog):
    caplog.set_level(logging.DEBUG)
    log.debug("Two %s %s", "onlyone")
    assert "Two" in caplog.text
