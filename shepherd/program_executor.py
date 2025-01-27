import os
import signal
import subprocess
import threading
import time
import logging

from shepherd.log_monitor import monitor_log_file
from shepherd.logging_setup import setup_logging


def execute_program(config, working_dir, state_dict, task_name, cond, state_times, start_time, pgid_dict,
                    stop_event, logging_queue):
    setup_logging(logging_queue)

    def signal_handler(signum, frame):
        logging.debug(f"Received signal {signum} in {task_name}")
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    command = config['command']
    stdout_path = config['stdout_path']
    stderr_path = config['stderr_path']

    dependencies = config.get('dependency', {}).get('items', {})
    dependency_mode = config.get('dependency', {}).get('mode', 'all')
    stdout_states = config.get('state', {}).get('log', {})
    file_path_to_monitor = config.get('state', {}).get('file', {}).get('path', '')
    file_states = config.get('state', {}).get('file', {}).get('states', {})

    file_dependencies = config.get('file_dependency', {})
    file_dependency_mode = file_dependencies.get('mode', 'all')
    file_dependency_items = file_dependencies.get('items', [])

    task_type = config.get('type', 'action')

    with cond:
        state_dict[task_name] = "initialized"
        update_state_time(task_name, "initialized", start_time, state_times)
        cond.notify_all()

    if file_dependencies:
        for file_dep in file_dependency_items:
            file_path = file_dep['path']
            min_size = file_dep.get('min_size', 1)

            while (not os.path.exists(file_path)
                   or (os.path.getsize(file_path) < min_size)) and not stop_event.is_set():

                time.sleep(0.5)

                if stop_event.is_set():
                    with cond:
                        state_dict[task_name] = "stopped_before_execution"
                        update_state_time(task_name, "stopped_before_execution", start_time, state_times)
                        cond.notify_all()
                    return

            logging.debug(f"Dependant file {file_path} found for task {task_name}")

    try:
        with cond:
            if dependency_mode == 'all':
                for dep_task, required_state in dependencies.items():
                    while required_state not in state_times.get(dep_task, {}) and not stop_event.is_set():
                        cond.wait()

            elif dependency_mode == 'any':
                satisfied = False
                while not satisfied and not stop_event.is_set():
                    for dep_task, required_state in dependencies.items():
                        if required_state in state_times.get(dep_task, {}):
                            satisfied = True
                            break
                    if not satisfied:
                        cond.wait()

        if stop_event.is_set():
            with cond:
                state_dict[task_name] = "stopped_before_execution"
                update_state_time(task_name, "stopped_before_execution", start_time, state_times)
                cond.notify_all()
            return

        logging.debug(f"Starting execution of '{task_type}' {task_name}")

        with cond:
            state_dict[task_name] = "started"
            update_state_time(task_name, "started", start_time, state_times)
            cond.notify_all()

        # Start the main log monitoring thread
        log_thread = threading.Thread(target=monitor_log_file,
                                      args=(stdout_path, state_dict, task_name, stdout_states, cond, state_times,
                                            start_time, stop_event))
        log_thread.start()

        # Optional: Start additional file monitoring thread if a file path is specified
        file_monitor_thread = None
        if file_path_to_monitor:
            file_monitor_thread = threading.Thread(target=monitor_log_file,
                                                   args=(
                                                       file_path_to_monitor, state_dict, task_name, file_states,
                                                       cond,
                                                       state_times, start_time, stop_event))
            file_monitor_thread.start()

        # Execute the process
        with open(stdout_path, 'w') as out, open(stderr_path, 'w') as err:
            process = subprocess.Popen(command, shell=True, cwd=working_dir, stdout=out, stderr=err,
                                       preexec_fn=os.setsid)
            pgid_dict[task_name] = os.getpgid(process.pid)

        while process.poll() is None:
            time.sleep(0.1)

        return_code = process.returncode

        logging.debug(f"Returned with code {return_code}")

        with cond:
            if stop_event.is_set() and return_code == -signal.SIGTERM:
                state_dict[task_name] = "stopped"
                update_state_time(task_name, "stopped", start_time, state_times)
                cond.notify_all()

        if task_type == 'service' and not stop_event.is_set():
            logging.debug(f"Stopping execution of '{task_type}' {task_name}")

            # If a service stops before receiving a stop event, mark it as failed
            with cond:
                state_dict[task_name] = "failure"
                update_state_time(task_name, "failure", start_time, state_times)
                cond.notify_all()
            logging.debug(f"ERROR: Task {task_name} stopped unexpectedly, marked as failure.")

        elif task_type == 'action':
            action_state = "action_success" if return_code == 0 else "action_failure"

            with cond:
                state_dict[task_name] = action_state
                update_state_time(task_name, action_state, start_time, state_times)
                cond.notify_all()

        with cond:
            state_dict[task_name] = "final"
            update_state_time(task_name, "final", start_time, state_times)
            cond.notify_all()

        if log_thread.is_alive():
            log_thread.join()

        if file_monitor_thread and file_monitor_thread.is_alive():
            file_monitor_thread.join()

    except Exception as e:
        logging.debug(f"Exception in executing {task_name}: {e}")

    logging.debug(f"Finished execution of {task_name}")


def update_state_time(task_name, state, start_time, state_times):
    current_time = time.time() - start_time

    local_state_times = state_times[task_name]
    local_state_times[state] = current_time
    state_times[task_name] = local_state_times

    logging.debug(f"Task '{task_name}' reached the state '{state}' at time {current_time:.3f}")
