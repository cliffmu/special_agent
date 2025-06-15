import logging
from typing import Any, Tuple

_LOGGER = logging.getLogger(__package__)


def _format(msg: str, args: Tuple[Any, ...]) -> str:
    """Safely format logging messages.

    Attempts standard %-style formatting. If that fails due to mismatched
    arguments, fall back to joining the string representation of each
    argument. This avoids ``logging`` errors when objects contain odd
    types or when the placeholder count is wrong.
    """

    if not args:
        return msg

    try:
        return msg % args
    except Exception:
        return " ".join([msg, *(repr(a) for a in args)])


def info(msg: str, *args: Any) -> None:
    _LOGGER.info(_format(msg, args))


def debug(msg: str, *args: Any) -> None:
    _LOGGER.debug(_format(msg, args))


def warning(msg: str, *args: Any) -> None:
    _LOGGER.warning(_format(msg, args))
