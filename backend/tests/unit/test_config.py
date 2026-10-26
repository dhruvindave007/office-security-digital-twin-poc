try:
    from src.config import Settings, get_settings
except ImportError:
    from config import Settings, get_settings


def test_settings_defaults(monkeypatch):
    """Test default settings configuration."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    settings = Settings()
    assert settings.app_name == "Office Vision Intelligence Platform"
    assert settings.app_env == "development"
    assert settings.debug is False          # no tracebacks to clients by default
    assert settings.host == "127.0.0.1"     # loopback unless deliberately exposed
    assert settings.api_token == ""
    assert settings.port == 8000
    assert "http://localhost:5173" in settings.cors_origins
    assert settings.database_url == "sqlite:///./data/digital_twin.db"
    assert settings.clips_directory == "./data/clips"
    assert settings.aws_region == "us-east-1"
    assert settings.bedrock_reasoning_model_id == "us.amazon.nova-lite-v1:0"
    assert settings.bedrock_fast_model_id == "us.amazon.nova-micro-v1:0"
    assert settings.bedrock_multimodal_model_id == "us.amazon.nova-lite-v1:0"
    assert settings.bedrock_connect_timeout == 10.0
    assert settings.bedrock_read_timeout == 60.0
    assert settings.bedrock_max_retries == 3
    assert settings.access_config_path == "data/access/doors.yaml"
    assert settings.vision_model == "yolo11n.pt"
    assert settings.vision_models_dir == "data/models"
    assert settings.vision_device == "cpu"
    assert settings.vision_tracker == "bytetrack.yaml"
    assert settings.vision_clips_dir == "data/clips"
    assert settings.vision_min_confidence == 0.35
    assert settings.vision_process_fps == 5.0
    assert settings.vision_benchmarks_dir == "data/benchmarks"


