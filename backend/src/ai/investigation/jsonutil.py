# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Parse a JSON object out of a model reply (tolerates a markdown fence or stray prose)."""
import json
import re
from typing import Any, Dict, Optional


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    match = re.search(r"\{.*\}", cleaned, re.S)
    for candidate in (cleaned, match.group(0) if match else None):
        if not candidate:
            continue
        try:
            doc = json.loads(candidate)
            if isinstance(doc, dict):
                return doc
        except (TypeError, ValueError):
            continue
    return None
