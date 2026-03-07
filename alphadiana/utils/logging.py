import logging
import sys


def setup_logging(run_id: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("alphadiana")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter(
            f"%(asctime)s [%(levelname)s] [run={run_id}] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
