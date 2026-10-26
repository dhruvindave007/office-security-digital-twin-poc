from functools import lru_cache
import json
from typing import List, Union

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Office Vision Intelligence Platform"
    app_env: str = "development"
    site_name: str = "Demo office"           # shown in the dashboard header, e.g. "HQ · floor 3"
    debug: bool = False                      # True shows tracebacks in API errors: never in production
    host: str = "127.0.0.1"                  # loopback by default; expose deliberately (with api_token)

    # Access control (src/api/security.py). Empty token -> only local connections are answered.
    # Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"
    api_token: str = Field(default="", validation_alias=AliasChoices("OVT_API_TOKEN", "API_TOKEN"))
    cookie_secure: bool = False              # True when served over HTTPS (adds Secure cookie + HSTS)
    session_hours: float = 12.0
    port: int = 8000
    cors_origins: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    database_url: str = "sqlite:///./data/digital_twin.db"
    clips_directory: str = "./data/clips"

    # Amazon Bedrock Configuration (Zero secrets; credentials loaded via AWS provider chain)
    aws_region: str = "us-east-1"
    bedrock_reasoning_model_id: str = "us.amazon.nova-lite-v1:0"
    bedrock_fast_model_id: str = "us.amazon.nova-micro-v1:0"
    bedrock_multimodal_model_id: str = "us.amazon.nova-lite-v1:0"  # cheapest video-capable model; Pro is ~13x the price
    # Incident investigation (M5). Haiku 5.5 chosen by measured eval: same grounding/honesty
    # scores as Sonnet 5.5 at ~1/14 of the cost. Budget cap guards spend on the POC account.
    bedrock_investigation_model_id: str = "global.anthropic.claude-haiku-5-5"
    # Claude Haiku 5.5 thinks by default (hidden tokens count as output). Measured 2026-10-13:
    # effort "low" passed every grounding/honesty check at ~20% less cost and latency than the
    # default "medium"; thinking disabled was cheapest but failed the identity-judgment check.
    investigation_effort: str = "low"
    investigation_max_output_tokens: int = 6000
    # Second, cheap pass that checks every claim against the records it cites (see claim_check.py).
    investigation_claim_judge: bool = True
    investigation_daily_budget_usd: float = 1.0
    # Automatic investigation of serious incidents (src/ai/investigation/auto.py). Off by default: it spends money.
    auto_investigate_enabled: bool = False
    auto_investigate_min_severity: str = "HIGH"
    auto_investigate_cooldown_seconds: float = 600.0   # one per (incident type, room) per 10 min
    auto_investigate_max_per_hour: int = 12
    bedrock_connect_timeout: float = 10.0
    bedrock_read_timeout: float = 60.0
    bedrock_max_retries: int = 3

    # Digital Twin Core Configuration
    twin_track_ttl_seconds: float = 30.0
    twin_snapshot_interval_events: int = 100
    twin_floorplan_seed_path: str = "src/twin/floorplan_seed.yaml"
    twin_prune_interval_seconds: float = 5.0
    twin_position_sample_seconds: float = 0.0   # live sites: 1.0 (store each person's position once a second)

    # Vision Geometry Configuration
    vision_calibration_dir: str = "data/calibration"
    vision_topology_dir: str = "data/topology"

    # Vision Pipeline & Tracking Configuration
    vision_model: str = "yolo11n.pt"
    vision_models_dir: str = "data/models"
    vision_device: str = "cpu"
    vision_tracker: str = "bytetrack.yaml"
    vision_clips_dir: str = "data/clips"
    vision_min_confidence: float = 0.35
    vision_process_fps: float = 5.0
    vision_benchmarks_dir: str = "data/benchmarks"

    # Rules & Simulator Configuration
    rules_policy_path: str = "data/rules/access_policy.yaml"

    # Live cameras (src/live). A site's camera list, e.g. ../data/sites/wisenet/live.yaml
    live_config_path: str = ""
    live_autostart: bool = False
    frontend_dist_dir: str = "../frontend/dist"   # built dashboard served at /, relative to backend/
    # Evidence clips recorded around serious live incidents (src/live/evidence.py)
    evidence_enabled: bool = True
    evidence_dir: str = "data/evidence"
    evidence_pre_seconds: float = 12.0
    evidence_post_seconds: float = 8.0
    evidence_min_severity: str = "HIGH"
    evidence_max_total_mb: float = 2000.0
    simulator_scenarios_dir: str = "data/scenarios"
    access_config_path: str = "data/access/doors.yaml"


    @field_validator("api_token")
    @classmethod
    def strong_token(cls, v: str) -> str:
        v = v.strip()
        if v and len(v) < 24:
            raise ValueError("OVT_API_TOKEN must be at least 24 characters (use secrets.token_urlsafe(32))")
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                try:
                    return json.loads(stripped)
                except Exception:
                    pass
            return [origin.strip() for origin in stripped.split(",") if origin.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
