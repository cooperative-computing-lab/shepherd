import os
import logging
import yaml


def load_and_preprocess_config(filepath):
    """Loads and preprocesses configuration from a YAML file."""
    if filepath is None or not os.path.exists(filepath):
        return None

    with open(filepath, 'r') as file:
        config = yaml.safe_load(file)

    preprocess_config(config, filepath)

    logging.debug(f"Loaded and preprocessed config from {filepath}")
    return config


def preprocess_config(config, config_path):
    """Automatically fills in missing stdout_path and stderr_path paths."""
    tasks = config.get('tasks', {})
    stdout_dir = config.get('output', {}).get('stdout_dir', '')
    working_dir = os.path.dirname(os.path.abspath(config_path))

    for task_name, details in tasks.items():
        # Auto-fill log and error files if not specified
        if 'stdout_path' not in details:
            details['stdout_path'] = f"{task_name}_stdout.log"
        if 'stderr_path' not in details:
            details['stderr_path'] = f"{task_name}_stderr.log"

        if stdout_dir:
            details['stdout_path'] = os.path.join(stdout_dir, details['stdout_path'])
            details['stderr_path'] = os.path.join(stdout_dir, details['stderr_path'])
        else:
            details['stdout_path'] = os.path.join(working_dir, details['stdout_path'])
            details['stderr_path'] = os.path.join(working_dir, details['stderr_path'])

        state_file_path = details.get('state', {}).get('file', {}).get('path', "")

        if state_file_path:
            details['state']['file']['path'] = os.path.join(working_dir, state_file_path)


def validate_and_sort_programs(config):
    logging.debug("Validating and sorting programs")
    required_keys = ['tasks']

    for key in required_keys:
        if key not in config:
            raise ValueError(f"Missing required key: {key}")

    tasks = config['tasks']

    for task, details in tasks.items():
        if 'command' not in details:
            raise ValueError(f"Program {task} is missing the 'command' key")
        if 'stdout_path' not in details:
            raise ValueError(f"Program {task} is missing the 'stdout_path' key")

    sorted_tasks = topological_sort(tasks)
    logging.debug(f"Sorted tasks: {sorted_tasks}")
    return sorted_tasks


def topological_sort(programs):
    logging.debug("Performing topological sort")

    graph = {program: details.get('dependency', {}).get('items', {}) for program, details in programs.items()}

    visited = set()
    visiting = set()
    stack = []

    def dfs(node):
        if node in visiting:
            raise ValueError(f"Cyclic dependency on {node}")

        visiting.add(node)

        if node not in visited:
            visited.add(node)
            for neighbor in graph[node]:
                dfs(neighbor)
            stack.append(node)

        visiting.remove(node)

    for program in graph:
        dfs(program)
    logging.debug(f"Topological sort result: {stack}")
    return stack
