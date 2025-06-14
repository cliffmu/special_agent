import logging

_LOGGER = logging.getLogger(__package__)

def info(msg: str, *args):
    _LOGGER.info(msg, *args)


def debug(msg: str, *args):
    _LOGGER.debug(msg, *args)


def warning(msg: str, *args):
    _LOGGER.warning(msg, *args)
