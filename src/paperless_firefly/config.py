"""
Configuration management.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

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
class ReconciliationConfig:
    """Reconciliation settings."""

    # Time window for fuzzy date matching (days)
    date_tolerance_days: int = 7
    # Minimum score to auto-confirm match
    auto_match_threshold: float = 0.90
    # Minimum score to show in proposals
    proposal_threshold: float = 0.60


@dataclass
class LLMConfig:
    """Local LLM (Ollama) configuration."""

    # Master enable/disable
    enabled: bool = False
    # Ollama server URL
    ollama_url: str = "http://localhost:11434"
    # Fast model (default)
    model_fast: str = "qwen2.5:3b-instruct"
    # Fallback model (for hard cases)
    model_fallback: str = "qwen2.5:7b-instruct"
    # Request timeout (seconds)
    timeout_seconds: int = 30
    # Max retries per request
    max_retries: int = 2
    # Cache TTL (days)
    cache_ttl_days: int = 30
    # Confidence threshold for green (auto-apply)
    green_threshold: float = 0.85
    # Number of initial suggestions to force yellow (calibration)
    calibration_count: int = 100


@dataclass
class Config:
    """Application configuration."""

    paperless: PaperlessConfig
    firefly: FireflyConfig
    reconciliation: ReconciliationConfig = field(default_factory=ReconciliationConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
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
    - SPARK_LLM_ENABLED (true/false)
    - OLLAMA_URL
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

    # Reconciliation config
    recon_data = data.get("reconciliation", {})
    reconciliation = ReconciliationConfig(
        date_tolerance_days=recon_data.get("date_tolerance_days", 7),
        auto_match_threshold=recon_data.get("auto_match_threshold", 0.90),
        proposal_threshold=recon_data.get("proposal_threshold", 0.60),
    )

    # LLM config
    llm_data = data.get("llm", {})
    llm_enabled_env = os.environ.get("SPARK_LLM_ENABLED", "").lower()
    llm_enabled = llm_data.get("enabled", False)
    if llm_enabled_env == "true":
        llm_enabled = True
    elif llm_enabled_env == "false":
        llm_enabled = False

    llm = LLMConfig(
        enabled=llm_enabled,
        ollama_url=os.environ.get(
            "OLLAMA_URL", llm_data.get("ollama_url", "http://localhost:11434")
        ),
        model_fast=llm_data.get("model_fast", "qwen2.5:3b-instruct"),
        model_fallback=llm_data.get("model_fallback", "qwen2.5:7b-instruct"),
        timeout_seconds=llm_data.get("timeout_seconds", 30),
        max_retries=llm_data.get("max_retries", 2),
        cache_ttl_days=llm_data.get("cache_ttl_days", 30),
        green_threshold=llm_data.get("green_threshold", 0.85),
        calibration_count=llm_data.get("calibration_count", 100),
    )

    # State DB
    state_db = data.get("state_db_path", "data/state.db")

    return Config(
        paperless=paperless,
        firefly=firefly,
        reconciliation=reconciliation,
        llm=llm,
        state_db_path=Path(state_db),
        auto_threshold=data.get("auto_threshold", 0.85),
        review_threshold=data.get("review_threshold", 0.60),
    )


def create_default_config(config_path: Path) -> None:
    """Create a default configuration file."""
    default_config = """# Paperless → Firefly III Pipeline Configuration (Spark)

paperless:
  base_url: "http://localhost:8000"
  token: "YOUR_PAPERLESS_TOKEN"
  filter_tag: "finance/inbox"

firefly:
  base_url: "http://localhost:8080"
  token: "YOUR_FIREFLY_TOKEN"
  default_source_account: "Checking Account"

# Reconciliation settings
reconciliation:
  date_tolerance_days: 7      # Match transactions within this window
  auto_match_threshold: 0.90  # Auto-confirm matches above this score
  proposal_threshold: 0.60    # Show proposals above this score

# Local LLM settings (Ollama)
llm:
  enabled: false                        # Set to true to enable LLM assist
  ollama_url: "http://localhost:11434"  # Ollama server URL
  model_fast: "qwen2.5:3b-instruct"     # Fast model for most cases
  model_fallback: "qwen2.5:7b-instruct" # Fallback for hard cases
  timeout_seconds: 30
  max_retries: 2
  cache_ttl_days: 30                    # Cache LLM results for this long
  green_threshold: 0.85                 # Auto-apply above this confidence
  calibration_count: 100                # Force review for first N suggestions

# State database path
state_db_path: "data/state.db"

# Confidence thresholds
auto_threshold: 0.85   # Above this: auto-import
review_threshold: 0.60  # Above this: review, below: manual
"""

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        f.write(default_config)
