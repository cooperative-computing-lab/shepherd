import multiprocessing
import os
import signal
import subprocess
import threading
import time
from multiprocessing.synchronize import Condition
from multiprocessing.synchronize import Event
from pathlib import Path
from typing import Any

from loguru import logger as log

from shepherd.log_monitor import monitor_log_file
from shepherd.logging_setup import setup_logging

console = None

try:
    from rich.console import Console
    from rich.traceback import install

    install()
    console = Console()
except ImportError:
    log.info("Install Rich to get better tracebacks.")


def execute_service(
    *,
    config: dict[str, Any],
    working_dir: str,
    state_dict: dict[str, str],
    service_name: str,
    cond: Condition,
    state_times: dict[str, dict[str, int]],
    start_time: float,
    pgid_dict: dict[str, int],
    stop_event: Event,
    logging_queue: multiprocessing.Queue,
):
    """Executes a service and updates the state dictionary."""
    setup_logging(logging_queue)

    def signal_handler(signum, frame) -> None:  # pylint: disable=unused-argument
        log.debug(f"Received signal {signum} in {service_name}")
        stop_event.set()

    signal.signal(signal.SIGINT, handler=signal_handler)
    signal.signal(signal.SIGTERM, handler=signal_handler)

    command = config["command"]

    # make sure out dirs exist
    stdout_path = Path(config["stdout_path"])
    stderr_path = Path(config["stderr_path"])
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    dependencies: dict[str, str] = config.get("dependency", {}).get("items", {})
    dependency_mode: str = config.get("dependency", {}).get("mode", "all")
    stdout_states: dict[str, str] = config.get("state", {}).get("log", {})
    file_path_to_monitor: str = config.get("state", {}).get("file", {}).get("path", "")
    file_states: dict[str, str] = (
        config.get("state", {}).get("file", {}).get("states", {})
    )

    service_type: str = config.get("type", "action")

    with cond:
        state_dict[service_name] = "initialized"
        update_state_time(
            service_name=service_name, state="initialized", state_times=state_times
        )
        cond.notify_all()

    try:
        with cond:
            if dependency_mode == "all":
                for dep_service, required_state in dependencies.items():
                    while (
                        required_state not in state_times.get(dep_service, {})
                        and not stop_event.is_set()
                    ):
                        cond.wait()

            elif dependency_mode == "any":
                satisfied = False
                while not satisfied and not stop_event.is_set():
                    for dep_service, required_state in dependencies.items():
                        if required_state in state_times.get(dep_service, {}):
                            satisfied = True
                            break
                    if not satisfied:
                        cond.wait()

        if stop_event.is_set():
            with cond:
                state_dict[service_name] = "stopped_before_execution"
                update_state_time(
                    service_name=service_name,
                    state="stopped_before_execution",
                    state_times=state_times,
                )
                cond.notify_all()
            return

        log.debug(f"Starting execution of '{service_type}' {service_name}")

        with cond:
            state_dict[service_name] = "started"
            update_state_time(
                service_name=service_name, state="started", state_times=state_times
            )
            cond.notify_all()

        # Start the main log monitoring thread
        log_monitor_thread = threading.Thread(
            target=monitor_log_file,
            kwargs={
                "log_path": stdout_path,
                "state_dict": state_dict,
                "service_name": service_name,
                "state_keywords": stdout_states,
                "cond": cond,
                "state_times": state_times,
                "start_time": start_time,
                "stop_event": stop_event,
            },
        )
        log_monitor_thread.start()

        # Optional: Start additional file monitoring thread if a file path is specified
        file_monitor_thread = None
        if file_path_to_monitor:
            file_monitor_thread = threading.Thread(
                target=monitor_log_file,
                kwargs={
                    "log_path": Path(file_path_to_monitor),
                    "state_dict": state_dict,
                    "service_name": service_name,
                    "state_keywords": file_states,
                    "cond": cond,
                    "state_times": state_times,
                    "start_time": start_time,
                    "stop_event": stop_event,
                },
            )
            file_monitor_thread.start()

        # Execute the process
        with (
            stdout_path.open(mode="w", encoding="utf-8") as out,
            stderr_path.open(mode="w", encoding="utf-8") as err,
        ):
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=working_dir,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
            pgid_dict[service_name] = os.getpgid(process.pid)

        while process.poll() is None:
            time.sleep(0.1)

        return_code = process.returncode

        msg = f"Service '{service_name}' returned with code {return_code}"
        _ = log.debug(msg) if return_code == 0 else log.error(msg)

        with cond:
            if stop_event.is_set() and return_code == -signal.SIGTERM:
                state_dict[service_name] = "stopped"
                update_state_time(
                    service_name=service_name, state="stopped", state_times=state_times
                )
                cond.notify_all()

        if service_type == "service" and not stop_event.is_set():
            log.debug(f"Stopping execution of '{service_type}' {service_name}")

            # If a service stops before receiving a stop event, mark it as failed
            with cond:
                state_dict[service_name] = "failure"
                update_state_time(
                    service_name=service_name, state="failure", state_times=state_times
                )
                cond.notify_all()
            log.error(
                f"Service '{service_name}' stopped unexpectedly, marked as failure.",
            )
            if process.stderr:
                log.error(
                    process.stderr.read().decode("utf-8", errors="ignore"),
                )
            if process.stdout:
                log.error(
                    process.stdout.read().decode("utf-8", errors="ignore"),
                )

        elif service_type == "action":
            action_state = "action_success" if return_code == 0 else "action_failure"

            with cond:
                state_dict[service_name] = action_state
                update_state_time(
                    service_name=service_name,
                    state=action_state,
                    state_times=state_times,
                )
                cond.notify_all()

        with cond:
            state_dict[service_name] = "final"
            update_state_time(
                service_name=service_name, state="final", state_times=state_times
            )
            cond.notify_all()

        if log_monitor_thread.is_alive():
            log_monitor_thread.join()

        if file_monitor_thread and file_monitor_thread.is_alive():
            file_monitor_thread.join()

    except Exception as err:  # pylint: disable=broad-except
        log.error(f"Exception in service '{service_name}': {err}")
        if console:
            console.print_exception(show_locals=True)
        else:
            log.warning("Install Rich to get better tracebacks.")
            log.exception(err)

    log.debug(f"Finished execution of service '{service_name}'")


def update_state_time(*, service_name, state, state_times) -> None:
    """Updates the state time for a service."""
    current_time = int(time.time() * 1000)

    local_state_times = state_times[service_name]
    local_state_times[state] = current_time
    state_times[service_name] = local_state_times

    log.debug(
        f"Service '{service_name}' reached state '{state.upper()}' at "
        f"{time.strftime('%H:%M:%S', time.localtime(current_time // 1_000))}",
    )
