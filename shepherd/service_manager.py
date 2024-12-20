"""Manages the execution of services and the stop conditions."""

import json
import multiprocessing
import os
import signal
import threading
import time
from multiprocessing import Process
from pathlib import Path
from typing import Any

from loguru import logger as log

from shepherd.config_loader import load_and_preprocess_config
from shepherd.config_loader import validate_and_sort_services
from shepherd.program_executor import execute_service


def save_state_times(state_times: dict[str, Any], output_file: Path) -> None:
    """Saves the state times to a JSON file."""

    output_file = Path(output_file)
    log.debug(state_times)

    state_times_dict = dict(state_times)

    for key, value in state_times_dict.items():
        state_times_dict[key] = dict(value)

    with output_file.open("w", encoding="utf-8") as fp:
        json.dump(state_times_dict, fp, indent=2)


class ServiceManager:
    """Service Manager for the Shepherd Workflow Manager.

    Args:
        run_dir:        The directory exclusive to this simulation run.
        config_path:    Path to the Shepherd config (YAML) file.
        working_dir:    Directory to where call the services / scripts from.
        logging_queue:  Multiprocessing queue for logging messages.
    """

    def __init__(
        self,
        run_dir: str | Path,
        config_path: str | Path,
        working_dir: str | Path,
        logging_queue,
    ) -> None:
        log.debug("Initializing ServiceManager")
        self.run_dir = Path(run_dir)
        if not self.run_dir.is_dir():
            msg = f"Run directory not found: {self.run_dir}"
            raise NotADirectoryError(msg)
        self.config_path = Path(config_path)
        self.config = load_and_preprocess_config(self.config_path)

        self.cond = multiprocessing.Condition()
        self.logging_queue = logging_queue
        self.max_run_time = self.config.get("max_run_time", None)
        self.output = self.config["output"]
        self.pgid_dict = multiprocessing.Manager().dict()
        self.process_timeout = self.config.get("process_timeout", 10)
        self.processes: dict[str, Process] = {}
        self.services: dict[str, dict[str, Any]] = self.config["services"]
        self.sorted_services = validate_and_sort_services(self.config)
        self.state_dict = multiprocessing.Manager().dict()
        self.state_times = multiprocessing.Manager().dict()
        self.stop_event = multiprocessing.Event()
        self.stop_signal_path: Path = (
            self.run_dir / "control" / self.config.get("stop_signal", "stop.txt")
        )
        self.working_dir = Path(working_dir)
        log.info("ServiceManager initialized")
        config = json.dumps(self.config, indent=4)
        msg = f"Shepherd (service mgr) configuration:\n{config}"
        log.debug(msg)

    def setup_signal_handlers(self) -> None:
        """Sets up signal handlers for SIGTERM and SIGINT."""
        log.debug("Setting up signal handlers")
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)

    def signal_handler(self, signum, frame) -> None:  # pylint: disable=unused-argument
        """Handles SIGTERM and SIGINT signals."""
        log.debug(
            f"Received signal {signum} in pid {os.getpid()}, stopping all services...",
        )
        self.stop_event.set()

    def start_services(self, start_time) -> None:
        """Starts all services in the workflow, clearing stop signal file if present."""
        log.debug("Starting Shepherd workflow services")
        self.setup_signal_handlers()

        for service in self.sorted_services:
            service_config = self.services[service]

            self.state_dict[service] = ""
            self.state_times[service] = {}

            log.info(f"Starting service '{service}'")
            p_exec = Process(
                target=execute_service,
                kwargs={
                    "config": service_config,
                    "working_dir": self.working_dir,
                    "state_dict": self.state_dict,
                    "service_name": service,
                    "cond": self.cond,
                    "state_times": self.state_times,
                    "start_time": start_time,
                    "pgid_dict": self.pgid_dict,
                    "stop_event": self.stop_event,
                    "logging_queue": self.logging_queue,
                },
            )

            p_exec.start()
            self.processes[service] = p_exec

        log.debug("All services initialized")

        stop_thread = threading.Thread(
            target=self.check_stop_conditions, args=(start_time,)
        )
        stop_thread.start()

        for p in self.processes.values():
            p.join()

        stop_thread.join()

        if self.stop_signal_path.is_file():
            self.stop_signal_path.unlink()

        save_state_times(
            state_times=self.state_times,  # pyright: ignore[reportArgumentType]
            output_file=self.working_dir / self.output["state_times"],
        )

    def check_stop_conditions(self, start_time: float) -> None:
        """Issues a stop event if any of the stop conditions are met."""
        log.debug("Checking stop conditions")
        while not self.stop_event.is_set():
            if (
                self.is_stop_signal_present()
                or self.is_runtime_exceeded(start_time)
                or self.is_all_services_final()
            ):
                self.stop_event.set()
            else:
                self.stop_event.wait(timeout=1)

        self.stop_all_services()

        log.debug("Finished checking stop conditions")

    def stop_all_services(self) -> None:
        """Stops all services in this workflow."""
        log.debug("Stopping all services")
        for service_name in self.processes:
            pgid = self.pgid_dict.get(service_name)
            if not pgid:
                continue
            try:
                os.killpg(pgid, signal.SIGTERM)
                msg = (
                    f"Sent SIGTERM to process group '{pgid}'"
                    f" for service '{service_name}'"
                )
                log.debug(msg)
            except (ProcessLookupError, OSError):
                msg = (
                    f"Process group '{pgid}' for service "
                    f"'{service_name}' not found or already terminated."
                )
                log.info(msg)

        failed_to_stop: list[str] = []
        for service_name, process in self.processes.items():
            process.join(timeout=self.process_timeout)
            if not process.is_alive():
                continue
            pgid = self.pgid_dict.get(service_name)
            if pgid:
                os.killpg(pgid, signal.SIGKILL)
                msg = (
                    f"Service '{service_name}' did not terminate in "
                    "time and was forcefully killed."
                )
                log.warning(msg)
            else:
                failed_to_stop.append(service_name)
                msg = (
                    f"Service '{service_name}' did not terminate in "
                    f"time and could not be forcefully killed."
                )
                log.warning(msg)

        if failed_to_stop:
            log.warning("Some services could not be stopped cleanly:")
            for service_name in failed_to_stop:
                log.warning(f"\tService '{service_name}' could not be stopped.")
        else:
            log.info("All services have been stopped")

    def stop_service(self, service_name: str) -> None:
        """Stops a specific service by name."""
        if service_name in self.processes:
            process = self.processes[service_name]
            if process.is_alive():
                pgid = self.pgid_dict.get(service_name)
                if pgid:
                    os.killpg(pgid, signal.SIGTERM)  # Terminate the process group

                process.terminate()
                process.join()
            log.info(f"Service '{service_name}' has been stopped.")
        else:
            log.warning(f"Service '{service_name}' not found.")

    def is_stop_signal_present(self) -> bool:
        """Checks if the stop signal file exists and is a file."""
        if self.stop_signal_path.is_file():
            log.debug("Received stop signal")
            return True
        return False

    def is_runtime_exceeded(self, start_time: float) -> bool:
        """Checks if the maximum runtime has been exceeded the maximum allowed."""
        if not self.max_run_time:
            return False
        current_time = time.time()
        if (current_time - start_time) > self.max_run_time:
            log.debug("Maximum runtime exceeded. Stopping all services.")
            return True
        return False

    def is_all_services_final(self) -> bool:
        """Checks if all services have reached the final state."""
        return all(state == "final" for state in self.state_dict.values())
