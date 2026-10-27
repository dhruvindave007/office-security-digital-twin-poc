# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import logging

import yaml

try:
    from src.core.paths import _resolve_data_path
except ImportError:
    from core.paths import _resolve_data_path

logger = logging.getLogger("office_vision.simulator.scenarios")


@dataclass
class ScenarioStep:
    """Individual observation or access-control step within a scenario definition."""

    t: float
    kind: str = "observation"
    # Observation fields:
    camera_id: Optional[str] = None
    track_id: Optional[str] = None
    floor: Optional[List[float]] = None
    confidence: float = 1.0
    person_id: Optional[str] = None
    # Access fields:
    door_id: Optional[str] = None
    badge_id: Optional[str] = None
    result: Optional[str] = None

    def __post_init__(self) -> None:
        self.t = float(self.t)
        self.confidence = float(self.confidence)
        self.kind = str(self.kind).strip().lower()

        if self.kind == "observation":
            if not self.camera_id or not str(self.camera_id).strip():
                raise ValueError("ScenarioStep camera_id cannot be empty.")
            if not self.track_id or not str(self.track_id).strip():
                raise ValueError("ScenarioStep track_id cannot be empty.")
            if not isinstance(self.floor, (list, tuple)) or len(self.floor) != 2:
                raise ValueError(f"ScenarioStep floor coordinates must be a list of 2 numbers [x, y], got {self.floor}.")
            try:
                self.floor = [float(self.floor[0]), float(self.floor[1])]
            except (ValueError, TypeError) as e:
                raise ValueError(f"ScenarioStep floor coordinates must contain numbers, got {self.floor}") from e
        elif self.kind == "access":
            if not self.door_id or not str(self.door_id).strip():
                raise ValueError("ScenarioStep door_id cannot be empty for access step.")
            if not self.badge_id or not str(self.badge_id).strip():
                raise ValueError("ScenarioStep badge_id cannot be empty for access step.")
            if not self.result or str(self.result).strip().upper() not in ("GRANTED", "DENIED"):
                raise ValueError(f"ScenarioStep result must be 'GRANTED' or 'DENIED' for access step, got '{self.result}'.")
            self.result = str(self.result).strip().upper()
        else:
            raise ValueError(f"Unknown scenario step kind '{self.kind}'. Must be 'observation' or 'access'.")


@dataclass
class Scenario:
    """Complete security scenario definition consisting of time-ordered observation/access steps."""

    name: str
    description: str
    base_time_utc: str
    steps: List[ScenarioStep] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Validate steps are strictly ordered by t
        for i in range(len(self.steps) - 1):
            if self.steps[i].t > self.steps[i + 1].t:
                raise ValueError(
                    f"Scenario '{self.name}' steps must be sorted by time offset 't'. "
                    f"Step {i} (t={self.steps[i].t}) is after step {i + 1} (t={self.steps[i + 1].t})."
                )


def load_scenario(path: Union[str, Path]) -> Scenario:
    """Load and validate a single scenario definition from a YAML file."""
    resolved_path = _resolve_data_path(str(path), str(path))
    if not resolved_path.exists():
        raise FileNotFoundError(f"Scenario file not found at: {path}")

    with open(resolved_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Scenario YAML must contain a dictionary mapping, got {type(data).__name__}")

    name = str(data.get("name", resolved_path.stem))
    description = str(data.get("description", ""))
    base_time_utc = str(data.get("base_time_utc", "2026-01-15T10:00:00Z"))

    raw_steps = data.get("steps", [])
    if not isinstance(raw_steps, list):
        raise ValueError(f"Scenario '{name}' steps must be a list, got {type(raw_steps).__name__}")

    steps: List[ScenarioStep] = []
    for s in raw_steps:
        if not isinstance(s, dict):
            raise ValueError(f"Scenario step must be a dictionary, got {s}")
        steps.append(
            ScenarioStep(
                t=s.get("t", 0.0),
                kind=s.get("kind", "observation"),
                camera_id=s.get("camera_id"),
                track_id=s.get("track_id"),
                floor=s.get("floor"),
                confidence=s.get("confidence", 1.0),
                person_id=s.get("person_id"),
                door_id=s.get("door_id"),
                badge_id=s.get("badge_id"),
                result=s.get("result"),
            )
        )

    return Scenario(
        name=name,
        description=description,
        base_time_utc=base_time_utc,
        steps=steps,
    )


def list_scenarios(dir_path: Union[str, Path] = "data/scenarios") -> List[Scenario]:
    """Discover and load all scenario YAML files within a directory."""
    resolved_dir = _resolve_data_path(str(dir_path), str(dir_path))
    if not resolved_dir.exists() or not resolved_dir.is_dir():
        return []

    scenarios: List[Scenario] = []
    for yaml_file in sorted(resolved_dir.glob("*.yaml")):
        try:
            scenarios.append(load_scenario(yaml_file))
        except Exception as e:
            # Keep listing the valid ones, but never hide a broken file silently.
            logger.warning("Skipping invalid scenario file %s: %s", yaml_file, e)
            continue

    return scenarios
