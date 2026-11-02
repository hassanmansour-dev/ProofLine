"""measured_evals — evaluation and security checks for LLM pipelines.

Standalone: this package never imports the proofline app (enforced by
tests/test_boundary.py), so other projects can depend on it unchanged.
"""

from importlib.metadata import version

__version__ = version("measured-evals")

__all__ = ["__version__"]
