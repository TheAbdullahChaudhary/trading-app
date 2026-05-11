"""Structured colored logger for the trading bot."""
import logging
import os
import sys
from datetime import datetime

try:
    import colorlog
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)

    # Console handler
    if HAS_COLOR:
        fmt = "%(log_color)s%(asctime)s [%(levelname)-8s] %(name)-20s%(reset)s %(message)s"
        formatter = colorlog.ColoredFormatter(
            fmt,
            datefmt="%H:%M:%S",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
    else:
        fmt = "%(asctime)s [%(levelname)-8s] %(name)-20s %(message)s"
        formatter = logging.Formatter(fmt, datefmt="%H:%M:%S")

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File handler
    os.makedirs("logs", exist_ok=True)
    log_file = f"logs/bot_{datetime.now().strftime('%Y%m%d')}.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)-20s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    return logger
