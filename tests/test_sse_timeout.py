"""Tests for SSE generator hard timeout.

Grok Elder Council finding (LOW): /api/logs/stream generator runs forever.
Add 5-minute hard timeout so connections are recycled.
"""
import pytest
from backend.main import SSE_HARD_TIMEOUT_SECONDS


def test_sse_timeout_constant_exists():
    """SSE hard timeout constant must be defined."""
    assert SSE_HARD_TIMEOUT_SECONDS == 300, \
        "SSE hard timeout should be 5 minutes (300 seconds)"
