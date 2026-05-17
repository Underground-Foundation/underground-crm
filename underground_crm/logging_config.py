import logging

_gray = "\x1b[38;20m"
_yellow = "\x1b[33;20m"
_red = "\x1b[31;20m"
_bold_red = "\x1b[31;1m"
_reset = "\x1b[0m"
_fmt = "%(levelname)s %(name)s:%(lineno)d| %(message)s"

_FORMATS = {
    logging.DEBUG: _gray + _fmt + _reset,
    logging.INFO: _fmt,
    logging.WARNING: _yellow + _fmt + _reset,
    logging.ERROR: _red + _fmt + _reset,
    logging.CRITICAL: _bold_red + _fmt + _reset,
}


class ColoredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return logging.Formatter(_FORMATS.get(record.levelno, _fmt)).format(record)
