# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import threading

# Reentrant lock governing all concurrent mutations to the in-memory Digital Twin state,
# SQLite event store, and rule engine caches.
TWIN_WRITE_LOCK = threading.RLock()
