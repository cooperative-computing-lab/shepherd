#!/usr/bin/env python3
import multiprocessing
import time
import logging
import logging.config
import logging.handlers
import argparse

from shepherd.service_manager import TaskManager
from shepherd.logging_setup import setup_logging, listener_process
from shepherd._version import __version__  # Import the version


def main():
    parser = argparse.ArgumentParser(description='Run Shepherd Workflow Manager')

    parser.add_argument('--config', '-c', type=str, help='Path to the program config YAML file')
    parser.add_argument('--log', '-l', type=str, default='shepherd.log', help='Path to the log file')
    parser.add_argument('--version', '-v', action='version', version=f'Shepherd {__version__}',
                        help='Show the version and exit')

    args = parser.parse_args()

    config_path = args.config
    log_file = args.log

    start_time = time.time()

    logging_queue = multiprocessing.Queue()
    listener = multiprocessing.Process(target=listener_process, args=(logging_queue, log_file))
    listener.start()
    setup_logging(logging_queue)

    logging.debug("Starting main")
    service_manager = TaskManager(config_path, logging_queue)
    service_manager.start_tasks(start_time)
    logging.debug("Exiting main")

    logging_queue.put(None)  # Send None to the listener to stop it
    listener.join()


if __name__ == '__main__':
    main()
