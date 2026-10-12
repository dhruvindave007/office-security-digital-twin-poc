try:
    from config import Settings
except ImportError:
    from src.config import Settings


def test_settings_defaults():
    """Test default settings configuration."""
    settings = Settings()
    assert settings.app_name == "Office Vision Intelligence Platform"
    assert settings.app_env == "development"
    assert settings.debug is True
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert "http://localhost:5173" in settings.cors_origins
    assert settings.database_url == "sqlite:///./data/digital_twin.db"
    assert settings.clips_directory == "./data/clips"
    assert settings.aws_region == "us-east-1"
    assert settings.bedrock_reasoning_model_id == "us.amazon.nova-lite-v1:0"
    assert settings.bedrock_fast_model_id == "us.amazon.nova-micro-v1:0"
    assert settings.bedrock_multimodal_model_id == "us.amazon.nova-pro-v1:0"
    assert settings.bedrock_connect_timeout == 10.0
    assert settings.bedrock_read_timeout == 60.0
    assert settings.bedrock_max_retries == 3

