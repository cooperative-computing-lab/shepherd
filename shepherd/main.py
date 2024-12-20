#!/usr/bin/env python3
"""Script to start a Shepherd Workflow Manager instance."""

import argparse
import multiprocessing
import time
from pathlib import Path

from loguru import logger as log

from shepherd import logging_setup
from shepherd.service_manager import ServiceManager

console = None

try:
    from rich.console import Console
    from rich.traceback import install

    install()
    console = Console()
except ImportError:
    log.info("Install Rich to get better tracebacks.")


def main() -> None:
    """Main entry point for the Shepherd Workflow Manager."""
    log.debug("Starting Shepherd Workflow Manager")
    parser = argparse.ArgumentParser(description="Run Shepherd Workflow Manager")
    parser.add_argument(
        "--run-dir",
        type=str,
        help="Writeable directory exclusive to this simulation run. Must exist.",
        required=True,
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to the Shepherd config (YAML) file. Must exist.",
        required=True,
    )
    parser.add_argument(
        "--work-dir",
        type=str,
        default=None,
        help="Directory to where call the services / scripts from. Must exist.",
    )
    parser.add_argument(
        "--log",
        type=str,
        default=None,
        help="Path to Shepherd's log file. File and parents will be created if not exist.",
    )

    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        msg = f"Run directory not found: {run_dir}"
        raise NotADirectoryError(msg)
    config_path = Path(args.config)
    working_dir = Path(args.work_dir) if args.work_dir else Path("/app/scripts")
    if not working_dir.is_dir():
        msg = (
            f"Working directory not found: {working_dir}. "
            "Either create it or set --work-dir when calling Shepherd."
        )
        raise NotADirectoryError(msg)
    log_file = Path(args.log) if args.log else run_dir / "logs" / "shepherd.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    start_time_fmt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start_time))
    log.debug(f"Start time: {start_time_fmt}")

    logging_queue = multiprocessing.Queue()
    listener = multiprocessing.Process(
        target=logging_setup.logger_daemon,
        kwargs={"queue": logging_queue, "log_file": log_file},
    )
    listener.start()
    logging_setup.setup_logging(logging_queue)

    log.debug("Starting main")
    service_manager = ServiceManager(
        config_path=config_path,
        logging_queue=logging_queue,
        run_dir=run_dir,
        working_dir=working_dir,
    )
    service_manager.start_services(start_time)
    log.debug("Exiting main")

    logging_queue.put(None)  # Send None to the listener to stop it
    listener.join()


if __name__ == "__main__":
    main()
