"""Monitors log files for specific keywords and updates the state dictionary."""

import time
from multiprocessing.synchronize import Condition
from multiprocessing.synchronize import Event
from pathlib import Path

from loguru import logger as log


def monitor_log_file(
    log_path: str | Path,
    state_dict: dict[str, str],
    service_name: str,
    state_keywords: dict[str, str],
    cond: Condition,
    state_times: dict[str, dict[str, float]],
    start_time: float,
    stop_event: Event,
) -> None:
    """Monitors a log file for specific keywords and updates the state dictionary."""

    if not state_keywords:
        log.warning(
            f"No state keywords for '{service_name}'; "
            "exiting log monitor for this service."
        )
        return

    log_path = Path(log_path)
    while not log_path.exists():
        if stop_event.is_set():
            log.info(
                f"Stop event set for service '{service_name}'; exiting log monitor"
            )
            return
        time.sleep(0.1)

    if not log_path.is_file():
        log.warning(
            f"Log path '{log_path}' for service '{service_name}' "
            "is not a file; exiting log monitor"
        )
        return

    log.debug(f"Started monitoring '{log_path}' for service '{service_name}'")
    last_state = list(state_keywords.keys())[-1]
    reached_last_state: bool = False

    with log_path.open(mode="r", encoding="utf-8") as log_file:
        while not stop_event.is_set():
            line = log_file.readline()
            if not line:
                time.sleep(0.05)
                continue

            current_time = time.time() - start_time
            reached_last_state = False

            for state, value in state_keywords.items():
                if value not in line:
                    continue
                with cond:
                    state_dict[service_name] = state
                    local_state_times = state_times[service_name]
                    local_state_times[state] = current_time
                    state_times[service_name] = local_state_times
                    cond.notify_all()

                    msg = (
                        f"Service '{service_name}' reached state "
                        f"'{state.upper()}' at {current_time}"
                    )
                    log.info(msg)

                    if state == last_state:
                        reached_last_state = True
                        break

            if reached_last_state:
                break

    log.info(f"Finished monitoring '{log_path}' for service '{service_name}'")
