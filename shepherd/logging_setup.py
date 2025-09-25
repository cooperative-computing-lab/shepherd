"""Logging setup for the main process and the listener process."""

import logging
import logging.config
import logging.handlers
import multiprocessing
import sys
import traceback
from pathlib import Path

from loguru import logger as log


def setup_logging(queue: multiprocessing.Queue) -> None:
    """Sets up logging for the main process."""
    root = logging.getLogger()
    handler = logging.handlers.QueueHandler(queue)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)


def configure_listener(log_file: str | Path | None = None) -> None:
    """Configures the listener process to write log messages to a file."""
    root = logging.getLogger()

    # default handler
    handler = logging.FileHandler(log_file) if log_file else logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(name)-12s %(levelname)-8s %(message)s")
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # stream handler
    # TODO: make it optional?
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    root.setLevel(logging.DEBUG)


def logger_daemon(queue: multiprocessing.Queue, log_file: str | Path | None) -> None:
    """Listens for log messages and writes them to a file."""
    configure_listener(log_file)
    while True:
        try:
            record = queue.get()
            if record is None:
                break
            logger = logging.getLogger(record.name)
            logger.handle(record)
        except Exception as err:  # pylint: disable=broad-except # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            log.error(f"Problem in logging listener: {err}")
