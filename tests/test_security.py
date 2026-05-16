"""Unit tests for tools/lib/security.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from lib.security import SecurityViolation  # noqa: E402


def test_security_violation_is_exception():
    exc = SecurityViolation("test reason")
    assert isinstance(exc, Exception)
    assert str(exc) == "test reason"
