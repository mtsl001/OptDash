"""
logger.py — Logging configuration for the OptDash pipeline.
"""
import logging
import logging.handlers
from pathlib import Path


def setup_logging(log_dir: Path = None, level: int = logging.INFO) -> None:
    """Configure root logger: console + rotating file handler."""
    root = logging.getLogger()
    if root.handlers:
        return  # Already configured

    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    if log_dir is None:
        log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "pipeline.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)
    root.addHandler(fh)
