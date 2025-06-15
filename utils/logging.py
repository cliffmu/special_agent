# import logging

# _LOGGER = logging.getLogger(__package__)

# def info(msg: str, *args):
#     _LOGGER.info(msg, *args)


# def debug(msg: str, *args):
#     _LOGGER.debug(msg, *args)


# def warning(msg: str, *args):
#     _LOGGER.warning(msg, *args)

import logging, re
_LOGGER = logging.getLogger("custom_components.special_agent")
_PLACEHOLDER_RE = re.compile(r"%\([^)]+\)|%[sdifr]")

def _safe(level, msg, *args, **kw):
    if args:
        try:                      # 1️⃣ classic %-style happy path
            if _PLACEHOLDER_RE.search(msg):
                _LOGGER.log(level, msg, *args, **kw)
                return
            # 2️⃣ brace‑style ?  configure a Formatter(style='{') once
            if '{' in msg and '}' in msg:
                _LOGGER.log(level, msg.format(*args), **kw)
                return
            # 3️⃣ no placeholders → just append repr()s
            msg = f"{msg} " + " ".join(repr(a) for a in args)
        except Exception:         # paranoia; never crash caller
            msg = f"{msg} " + " ".join(repr(a) for a in args)
    _LOGGER.log(level, msg, **kw)

def debug(msg, *args, **kw):   _safe(logging.DEBUG,    msg, *args, **kw)
def info(msg,  *args, **kw):   _safe(logging.INFO,     msg, *args, **kw)
def warning(msg,*args, **kw):  _safe(logging.WARNING,  msg, *args, **kw)
def error(msg, *args, **kw):   _safe(logging.ERROR,    msg, *args, **kw)

