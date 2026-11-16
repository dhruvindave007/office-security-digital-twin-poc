# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Live smoke tests call real, billable AWS services, so they only run when explicitly requested.

Run them with:  OVT_LIVE_AWS_TESTS=1 pytest tests/smoke
"""
import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("OVT_LIVE_AWS_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="live AWS smoke test; set OVT_LIVE_AWS_TESTS=1 to run")
    for item in items:
        if "tests/smoke" in item.nodeid.replace("\\", "/"):
            item.add_marker(skip)
