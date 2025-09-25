from pathlib import Path

from setuptools import find_packages
from setuptools import setup

# Read the version from _version.py
version = {}
version_path = Path(__file__).parent / "shepherd" / "_version.py"
with version_path.open(mode="r", encoding="utf-8") as fp:
    exec(fp.read(), version)

setup(
    version=version["__version__"],  # Use the imported version
    description="Shepherd - Service Orchestration and Monitoring Tool",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "pyyaml",
        "jinja2",
        "graphviz",
        "matplotlib",
        "pandas",
    ],
)
