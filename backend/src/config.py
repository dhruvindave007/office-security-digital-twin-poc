from functools import lru_cache
import json
from typing import List, Union

from pydantic import field_validator
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
    debug: bool = True
    host: str = "0.0.0.0"
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
    bedrock_connect_timeout: float = 10.0
    bedrock_read_timeout: float = 60.0
    bedrock_max_retries: int = 3

    # Digital Twin Core Configuration
    twin_track_ttl_seconds: float = 30.0
    twin_snapshot_interval_events: int = 100
    twin_floorplan_seed_path: str = "src/twin/floorplan_seed.yaml"
    twin_prune_interval_seconds: float = 5.0

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
    simulator_scenarios_dir: str = "data/scenarios"
    access_config_path: str = "data/access/doors.yaml"


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
