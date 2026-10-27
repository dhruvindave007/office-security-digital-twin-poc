# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import pytest
import yaml

from src.rules.policy import load_access_policy


def test_load_valid_access_policy():
    """Verify that the shipped default access policy file loads correctly."""
    policy = load_access_policy("data/rules/access_policy.yaml")
    assert policy.version >= 1
    assert "zone_server_room" in policy.restricted_zones
    assert policy.restricted_zones["zone_server_room"].min_security_level == 4
    assert "person_it_admin_01" in policy.restricted_zones["zone_server_room"].authorized_person_ids
    assert policy.business_hours_utc.start == "08:00"
    assert policy.business_hours_utc.end == "19:00"
    assert policy.dwell_alert_min_security_level == 3


def test_missing_policy_file_raises_filenotfound():
    """Verify that a missing policy file raises FileNotFoundError containing the target path."""
    target_path = "data/rules/does_not_exist_policy.yaml"
    with pytest.raises(FileNotFoundError) as exc_info:
        load_access_policy(target_path)
    assert target_path in str(exc_info.value)


@pytest.mark.parametrize(
    "bad_time",
    ["8:00", "25:00", "ab:cd"],
)
def test_invalid_business_hours_raises_value_error(tmp_path, bad_time):
    """Verify that malformed or out-of-range HH:MM values raise ValueError."""
    policy_data = {
        "version": 1,
        "business_hours_utc": {
            "start": bad_time,
            "end": "19:00",
        },
    }
    p = tmp_path / f"policy_bad_{bad_time.replace(':', '_')}.yaml"
    p.write_text(yaml.safe_dump(policy_data), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid business_hours_utc"):
        load_access_policy(p)


def test_unknown_top_level_keys_are_ignored(tmp_path):
    """Verify that unexpected top-level configuration keys do not cause errors."""
    policy_data = {
        "version": 1,
        "unexpected_key_1": "some_value",
        "custom_settings": {"foo": "bar", "count": 42},
        "business_hours_utc": {
            "start": "08:00",
            "end": "18:00",
        },
        "dwell_alert_min_security_level": 2,
    }
    p = tmp_path / "policy_extra_keys.yaml"
    p.write_text(yaml.safe_dump(policy_data), encoding="utf-8")

    policy = load_access_policy(p)
    assert policy.version == 1
    assert policy.business_hours_utc.start == "08:00"
    assert policy.business_hours_utc.end == "18:00"
    assert policy.dwell_alert_min_security_level == 2
    assert policy.correlation.badge_entry_window_seconds == 15.0
    assert policy.correlation.tailgating_window_seconds == 10.0
    assert policy.correlation.transit_slack_seconds == 2.0


def test_correlation_policy_defaults_when_missing(tmp_path):
    """Verify that omitting correlation section uses default parameters."""
    policy_data = {
        "version": 1,
        "restricted_zones": {},
    }
    p = tmp_path / "policy_no_corr.yaml"
    p.write_text(yaml.safe_dump(policy_data), encoding="utf-8")

    policy = load_access_policy(p)
    assert policy.correlation.badge_entry_window_seconds == 15.0
    assert policy.correlation.tailgating_window_seconds == 10.0
    assert policy.correlation.transit_slack_seconds == 2.0
