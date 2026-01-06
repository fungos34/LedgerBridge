"""
Configuration management.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class PaperlessConfig:
    """Paperless-ngx configuration."""

    base_url: str
    token: str
    filter_tag: str = "finance/inbox"


@dataclass
class FireflyConfig:
    """Firefly III configuration."""

    base_url: str
    token: str
    default_source_account: str = "Checking Account"


@dataclass
class Config:
    """Application configuration."""

    paperless: PaperlessConfig
    firefly: FireflyConfig
    state_db_path: Path = field(default_factory=lambda: Path("data/state.db"))

    # Confidence thresholds
    auto_threshold: float = 0.85
    review_threshold: float = 0.60


def load_config(config_path: Path) -> Config:
    """
    Load configuration from YAML file.

    Environment variables can override config values:
    - PAPERLESS_URL
    - PAPERLESS_TOKEN
    - FIREFLY_URL
    - FIREFLY_TOKEN
    """
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    # Paperless config
    paperless_data = data.get("paperless", {})
    paperless = PaperlessConfig(
        base_url=os.environ.get(
            "PAPERLESS_URL", paperless_data.get("base_url", "http://localhost:8000")
        ),
        token=os.environ.get("PAPERLESS_TOKEN", paperless_data.get("token", "")),
        filter_tag=paperless_data.get("filter_tag", "finance/inbox"),
    )

    # Firefly config
    firefly_data = data.get("firefly", {})
    firefly = FireflyConfig(
        base_url=os.environ.get(
            "FIREFLY_URL", firefly_data.get("base_url", "http://localhost:8080")
        ),
        token=os.environ.get("FIREFLY_TOKEN", firefly_data.get("token", "")),
        default_source_account=firefly_data.get("default_source_account", "Checking Account"),
    )

    # State DB
    state_db = data.get("state_db_path", "data/state.db")

    return Config(
        paperless=paperless,
        firefly=firefly,
        state_db_path=Path(state_db),
        auto_threshold=data.get("auto_threshold", 0.85),
        review_threshold=data.get("review_threshold", 0.60),
    )


def create_default_config(config_path: Path) -> None:
    """Create a default configuration file."""
    default_config = """# Paperless → Firefly III Pipeline Configuration

paperless:
  base_url: "http://localhost:8000"
  token: "YOUR_PAPERLESS_TOKEN"
  filter_tag: "finance/inbox"

firefly:
  base_url: "http://localhost:8080"
  token: "YOUR_FIREFLY_TOKEN"
  default_source_account: "Checking Account"

# State database path
state_db_path: "data/state.db"

# Confidence thresholds
auto_threshold: 0.85   # Above this: auto-import
review_threshold: 0.60  # Above this: review, below: manual
"""

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        f.write(default_config)
