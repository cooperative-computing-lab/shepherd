"""Helpers to load and preprocess Shepherd configuration."""

from pathlib import Path
from typing import Any

import yaml
from loguru import logger as log


def load_and_preprocess_config(filepath: Path) -> dict[str, Any]:
    """Loads and preprocesses configuration from a YAML file."""
    if filepath is None:
        return None
    if not filepath.exists():
        msg = f"Config file not found: {filepath}"
        raise FileNotFoundError(msg)
    if not filepath.is_file():
        msg = f"Config path is not a file: {filepath}"
        raise ValueError(msg)

    with filepath.open(mode="r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    preprocess_config(config, config_path=filepath)

    log.debug(f"Loaded and preprocessed config from {filepath}")
    return config


def preprocess_config(config: dict[str, Any], config_path: Path) -> None:
    """Automatically fills in missing stdout_path and stderr_path paths."""
    services = config.get("services", {})
    stdout_dir = config.get("output", {}).get("stdout_dir", "")
    stdout_dir = Path(stdout_dir) if stdout_dir else None
    working_dir = config_path.parent

    for service_name, details in services.items():
        # Auto-fill log and error files if not specified
        if "stdout_path" not in details:
            details["stdout_path"] = f"{service_name}_stdout.log"
        if "stderr_path" not in details:
            details["stderr_path"] = f"{service_name}_stderr.log"

        if not stdout_dir:
            log.warning(f"Service '{service_name}' has no 'stdout_dir' specified.")
        dir_used = stdout_dir if stdout_dir else working_dir
        details["stdout_path"] = str(dir_used / details["stdout_path"])
        details["stderr_path"] = str(dir_used / details["stderr_path"])

        state_file_path = details.get("state", {}).get("file", {}).get("path", "")

        if state_file_path:
            details["state"]["file"]["path"] = str(working_dir / state_file_path)


def validate_and_sort_services(config: dict[str, Any]) -> list[str]:
    """Validates and sorts services based on their dependencies."""
    log.debug("Validating and sorting services")
    required_keys = ["services"]

    for key in required_keys:
        if key not in config:
            msg = f"Missing required key: {key}"
            raise ValueError(msg)

    services = config["services"]

    for service, details in services.items():
        if "command" not in details:
            msg = f"Service '{service}' is missing the 'command' key"
            raise ValueError(msg)
        if "stdout_path" not in details:
            msg = f"Service '{service}' is missing the 'stdout_path' key"
            raise ValueError(msg)

    sorted_services = topological_sort(services)
    log.debug(f"Sorted services: {sorted_services}")
    return sorted_services


def topological_sort(services: dict[str, dict[str, Any]]) -> list[str]:
    """Forms a graph of services and dependencies, sorting them topologically."""
    log.debug("Performing topological sort")

    graph = {
        service: details.get("dependency", {}).get("items", {})
        for service, details in services.items()
    }

    visited: set[str] = set()
    visiting: set[str] = set()
    stack: list[str] = []

    def dfs(node) -> None:
        """Depth-first search for topological sort."""
        if node in visiting:
            msg = f"Cyclic dependency on {node}"
            raise ValueError(msg)

        visiting.add(node)

        if node not in visited:
            visited.add(node)
            for neighbor in graph[node]:
                dfs(neighbor)
            stack.append(node)

        visiting.remove(node)

    for service in graph:
        dfs(service)
    log.debug(f"Topological sort result: {stack}")
    return stack
