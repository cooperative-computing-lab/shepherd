import json
import logging
import multiprocessing
import os
import signal
import subprocess
import threading
import time
from multiprocessing import Process

from shepherd.config_loader import load_and_preprocess_config, validate_and_sort_programs
from shepherd.program_executor import execute_program


def save_state_times(state_times, output_file):
    logging.debug(state_times)

    state_times_dict = dict(state_times)

    for key, value in state_times_dict.items():
        state_times_dict[key] = dict(value)

    with open(output_file, 'w') as f:
        json.dump(state_times_dict, f, indent=2)


class TaskManager:
    def __init__(self, config_path, logging_queue):
        logging.debug("Initializing TaskManager")
        self.config = load_and_preprocess_config(config_path)
        self.tasks = self.config['tasks']
        self.sorted_tasks = validate_and_sort_programs(self.config)
        self.working_dir = os.path.dirname(os.path.abspath(config_path))
        self.output = self.config['output']
        self.stop_signal_path = os.path.join(self.working_dir, self.config.get('stop_signal', ''))
        self.max_run_time = self.config.get('max_run_time', None)
        self.stop_event = multiprocessing.Event()
        self.pgid_dict = multiprocessing.Manager().dict()
        self.state_dict = multiprocessing.Manager().dict()
        self.state_times = multiprocessing.Manager().dict()
        self.cond = multiprocessing.Condition()
        self.processes = {}
        self.logging_queue = logging_queue
        self.cleanup_command = self.config.get('cleanup_command', None)
        logging.debug("TaskManager initialized")

    def setup_signal_handlers(self):
        logging.debug("Setting up signal handlers")
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)

    def signal_handler(self, signum, frame):
        logging.debug(f"Received signal {signum} in pid {os.getpid()}, stopping all tasks...")
        self.stop_event.set()

    def start_tasks(self, start_time):
        logging.debug("Starting tasks")
        self.setup_signal_handlers()

        for task in self.sorted_tasks:
            task_config = self.tasks[task]

            self.state_dict[task] = ""
            self.state_times[task] = {}

            p_exec = Process(target=execute_program, args=(
                task_config, self.working_dir, self.state_dict, task, self.cond, self.state_times, start_time,
                self.pgid_dict, self.stop_event, self.logging_queue))

            p_exec.start()
            self.processes[task] = p_exec

        logging.debug("All tasks initialized")

        stop_thread = threading.Thread(target=self.check_stop_conditions, args=(start_time,))
        stop_thread.start()

        for p in self.processes.values():
            p.join()

        stop_thread.join()

        if os.path.isfile(self.stop_signal_path) and os.path.exists(self.stop_signal_path):
            os.remove(self.stop_signal_path)

        save_state_times(self.state_times, os.path.join(self.working_dir, self.output['state_times']))

    def check_stop_conditions(self, start_time):
        logging.debug("Checking stop conditions")
        while not self.stop_event.is_set():
            if (self.check_stop_signal_file()
                    or self.check_max_run_time(start_time) or self.check_all_tasks_final()):
                self.stop_event.set()
            else:
                self.stop_event.wait(timeout=1)

        self.stop_all_tasks()

        logging.debug("Finished checking stop conditions")

    def stop_all_tasks(self):
        logging.debug("Stopping all tasks")

        if self.cleanup_command:
            try:
                logging.debug(f"Executing system cleanup command: {self.cleanup_command}")

                subprocess.run(self.cleanup_command, shell=True, check=True)
                logging.debug("System cleanup command executed successfully")
            except subprocess.CalledProcessError as e:
                logging.error(f"System cleanup command failed: {e}")

        for task_name, process in self.processes.items():
            pgid = self.pgid_dict.get(task_name)
            if pgid:
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    logging.debug(f"Process group {pgid} for task {task_name} not found.")
            # process.terminate()

        for process in self.processes.values():
            process.join()

        logging.debug("All tasks have been stopped")

    def stop_task(self, task_name):
        if task_name in self.processes:
            process = self.processes[task_name]
            if process.is_alive():
                pgid = self.pgid_dict.get(task_name)
                if pgid:
                    os.killpg(pgid, signal.SIGTERM)  # Terminate the process group

                process.terminate()
                process.join()
            logging.debug(f"Task {task_name} has been stopped.")
        else:
            logging.debug(f"Task {task_name} not found.")

    def check_stop_signal_file(self):
        if os.path.exists(self.stop_signal_path) and os.path.isfile(self.stop_signal_path):
            logging.debug("Received stop signal")
            return True

    def check_max_run_time(self, start_time):
        if self.max_run_time:
            current_time = time.time()
            if (current_time - start_time) > self.max_run_time:
                logging.debug("Maximum runtime exceeded. Stopping all tasks.")
                return True
        return False

    def check_all_tasks_final(self):
        for state in self.state_dict.values():
            if state != 'final':
                return False
        return True
