"""Security gates for cleo package installation.

Pure validators. No I/O side-effects beyond reading paths the caller asks
about. Each gate raises SecurityViolation on failure; cleo.py catches and
converts to a CLI error.
"""
from __future__ import annotations


class SecurityViolation(Exception):
    """Raised when a package fails a hard security gate."""
