"""
Configuration management (SSOT).

This module defines ALL configuration for the Spark/LedgerBridge application.
All config keys are defined here; no other module should invent config keys.

Key invariants:
- Internal URLs (base_url) are for API calls (Docker network, localhost)
- External URLs (external_url) are for browser links (user-accessible)
- Never leak internal URLs into human-facing fields (Firefly notes, etc.)
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""

    pass


@dataclass
class PaperlessConfig:
    """Paperless-ngx configuration.

    SSOT for Paperless URL handling:
    - base_url: Internal URL for API calls (e.g., http://paperless:8000 in Docker)
    - external_url: Browser-accessible URL (e.g., https://paperless.example.com)

    If external_url is not set, falls back to base_url.
    """

    base_url: str
    token: str
    filter_tag: str = "finance/inbox"
    # External URL for browser links (SSOT for human-facing URLs)
    external_url: str | None = None

    def get_external_url(self) -> str:
        """Get the URL for browser links (external or fallback to base)."""
        return self.external_url or self.base_url


@dataclass
class FireflyConfig:
    """Firefly III configuration.

    SSOT for Firefly URL handling:
    - base_url: Internal URL for API calls
    - external_url: Browser-accessible URL
    """

    base_url: str
    token: str
    default_source_account: str = "Checking Account"
    # External URL for browser links
    external_url: str | None = None

    def get_external_url(self) -> str:
        """Get the URL for browser links."""
        return self.external_url or self.base_url


@dataclass
class ReconciliationConfig:
    """Reconciliation settings."""

    # Time window for fuzzy date matching (days)
    date_tolerance_days: int = 7
    # Default sync window for fetching Firefly transactions (days back from today)
    sync_days: int = 90
    # Minimum score to auto-confirm match (lowered from 0.90 to allow more auto-linking)
    auto_match_threshold: float = 0.75
    # Minimum score to show in proposals (lowered from 0.60 to show more suggestions)
    proposal_threshold: float = 0.30
    # Bank-first mode: require existing tx or explicit manual confirmation
    bank_first_mode: bool = True
    # Allow creation of new transactions only with explicit confirmation
    require_manual_confirmation_for_new: bool = True


@dataclass
class LLMConfig:
    """Local LLM (Ollama) configuration.

    SSOT for LLM settings:
    - enabled: Master switch (default OFF)
    - ollama_url: Can be localhost, LAN IP, or remote URL
    - auth_header: Optional auth header for proxied deployments
    - max_concurrent: Concurrency limiter for queue management
    """

    # Master enable/disable (SSOT: single enforcement point)
    enabled: bool = False
    # Ollama server URL (supports localhost, LAN, remote)
    ollama_url: str = "http://localhost:11434"
    # Optional authentication header for proxied deployments
    # Format: "Bearer <token>" or custom header value
    auth_header: str | None = None
    # Fast model (default)
    model_fast: str = "qwen2.5:3b-instruct-q4_K_M"
    # Fallback model (for hard cases)
    model_fallback: str = "qwen2.5:7b-instruct-q4_K_M"
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
    # Maximum concurrent LLM requests (queue/semaphore)
    max_concurrent: int = 2

    def is_remote(self) -> bool:
        """Check if Ollama URL is remote (not localhost)."""
        url_lower = self.ollama_url.lower()
        return not any(
            local in url_lower
            for local in ["localhost", "127.0.0.1", "::1", "host.docker.internal"]
        )


@dataclass
class AmountValidationConfig:
    """Amount validation settings (SSOT)."""

    # Require positive amounts in all inputs
    require_positive: bool = True
    # Allow automatic sign normalization (flip type if negative)
    allow_sign_normalization: bool = False
    # Maximum amount value (sanity check)
    max_amount: float = 1_000_000.0


@dataclass
class Config:
    """Application configuration (SSOT).

    All configuration is centralized here. No other module should define
    configuration keys or defaults.
    """

    paperless: PaperlessConfig
    firefly: FireflyConfig
    reconciliation: ReconciliationConfig = field(default_factory=ReconciliationConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    amount_validation: AmountValidationConfig = field(default_factory=AmountValidationConfig)
    state_db_path: Path = field(default_factory=lambda: Path("data/state.db"))

    # Confidence thresholds
    auto_threshold: float = 0.85
    review_threshold: float = 0.60

    def validate(self) -> list[str]:
        """Validate configuration completeness and consistency.

        Returns:
            List of validation errors (empty if valid)
        """
        errors: list[str] = []

        # Required URLs
        if not self.paperless.base_url:
            errors.append("paperless.base_url is required")
        if not self.firefly.base_url:
            errors.append("firefly.base_url is required")

        # If LLM is enabled, validate its settings
        if self.llm.enabled:
            if not self.llm.ollama_url:
                errors.append("llm.ollama_url is required when LLM is enabled")
            if self.llm.is_remote() and not self.llm.auth_header:
                # Warning, not error: remote without auth is allowed but logged
                pass

        # Thresholds must be sensible
        if self.reconciliation.auto_match_threshold < self.reconciliation.proposal_threshold:
            errors.append("auto_match_threshold must be >= proposal_threshold")

        return errors


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
    - OLLAMA_MODEL (fast model name)
    - OLLAMA_MODEL_FALLBACK (fallback model name)
    - OLLAMA_TIMEOUT (request timeout in seconds)
    - SPARK_LLM_CACHE_TTL_DAYS (cache expiry)
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
        external_url=os.environ.get("PAPERLESS_EXTERNAL_URL", paperless_data.get("external_url")),
    )

    # Firefly config
    firefly_data = data.get("firefly", {})
    firefly = FireflyConfig(
        base_url=os.environ.get(
            "FIREFLY_URL", firefly_data.get("base_url", "http://localhost:8080")
        ),
        token=os.environ.get("FIREFLY_TOKEN", firefly_data.get("token", "")),
        default_source_account=firefly_data.get("default_source_account", "Checking Account"),
        external_url=os.environ.get("FIREFLY_EXTERNAL_URL", firefly_data.get("external_url")),
    )

    # Reconciliation config
    recon_data = data.get("reconciliation", {})
    sync_days_env = os.environ.get("SPARK_RECONCILIATION_SYNC_DAYS", "")
    sync_days = recon_data.get("sync_days", 90)
    if sync_days_env:
        try:
            sync_days = int(sync_days_env)
        except ValueError:
            pass  # Keep default

    reconciliation = ReconciliationConfig(
        date_tolerance_days=recon_data.get("date_tolerance_days", 7),
        sync_days=sync_days,
        auto_match_threshold=recon_data.get("auto_match_threshold", 0.90),
        proposal_threshold=recon_data.get("proposal_threshold", 0.60),
        bank_first_mode=recon_data.get("bank_first_mode", True),
        require_manual_confirmation_for_new=recon_data.get(
            "require_manual_confirmation_for_new", True
        ),
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
        auth_header=os.environ.get("OLLAMA_AUTH_HEADER", llm_data.get("auth_header")),
        model_fast=os.environ.get(
            "OLLAMA_MODEL", llm_data.get("model_fast", "qwen2.5:3b-instruct-q4_K_M")
        ),
        model_fallback=os.environ.get(
            "OLLAMA_MODEL_FALLBACK", llm_data.get("model_fallback", "qwen2.5:7b-instruct-q4_K_M")
        ),
        timeout_seconds=int(os.environ.get(
            "OLLAMA_TIMEOUT", llm_data.get("timeout_seconds", 30)
        )),
        max_retries=llm_data.get("max_retries", 2),
        cache_ttl_days=int(os.environ.get(
            "SPARK_LLM_CACHE_TTL_DAYS", llm_data.get("cache_ttl_days", 30)
        )),
        green_threshold=llm_data.get("green_threshold", 0.85),
        calibration_count=llm_data.get("calibration_count", 100),
        max_concurrent=llm_data.get("max_concurrent", 2),
    )

    # Amount validation
    amount_data = data.get("amount_validation", {})
    amount_validation = AmountValidationConfig(
        require_positive=amount_data.get("require_positive", True),
        allow_sign_normalization=amount_data.get("allow_sign_normalization", False),
        max_amount=amount_data.get("max_amount", 1_000_000.0),
    )

    # State DB
    state_db = data.get("state_db_path", "data/state.db")

    return Config(
        paperless=paperless,
        firefly=firefly,
        reconciliation=reconciliation,
        llm=llm,
        amount_validation=amount_validation,
        state_db_path=Path(state_db),
        auto_threshold=data.get("auto_threshold", 0.85),
        review_threshold=data.get("review_threshold", 0.60),
    )


def create_default_config(config_path: Path) -> None:
    """Create a default configuration file."""
    default_config = """# Paperless → Firefly III Pipeline Configuration (Spark)
#
# URL Configuration (SSOT):
# - base_url: Internal URL for API calls (e.g., Docker network names)
# - external_url: Browser-accessible URL for human-facing links
#
# If external_url is not set, base_url is used for both.

paperless:
  base_url: "http://localhost:8000"      # Internal API URL
  external_url: null                      # Browser URL (set if different from base_url)
  token: "YOUR_PAPERLESS_TOKEN"
  filter_tag: "finance/inbox"

firefly:
  base_url: "http://localhost:8080"       # Internal API URL
  external_url: null                      # Browser URL (set if different from base_url)
  token: "YOUR_FIREFLY_TOKEN"
  default_source_account: "Checking Account"

# Reconciliation settings (bank-first by default)
reconciliation:
  date_tolerance_days: 7                   # Match transactions within this window
  auto_match_threshold: 0.90               # Auto-confirm matches above this score
  proposal_threshold: 0.60                 # Show proposals above this score
  bank_first_mode: true                    # Check for existing bank tx before creating
  require_manual_confirmation_for_new: true  # Require explicit confirmation for new tx

# Local LLM settings (Ollama)
# Supports localhost, LAN, or remote deployments
llm:
  enabled: false                           # Set to true to enable LLM assist
  ollama_url: "http://localhost:11434"     # Ollama server URL (localhost, LAN, or remote)
  auth_header: null                        # Optional auth header for proxied deployments
  model_fast: "qwen2.5:3b-instruct-q4_K_M"        # Fast model for most cases
  model_fallback: "qwen2.5:7b-instruct-q4_K_M"    # Fallback for hard cases
  timeout_seconds: 30
  max_retries: 2
  cache_ttl_days: 30                       # Cache LLM results for this long
  green_threshold: 0.85                    # Auto-apply above this confidence
  calibration_count: 100                   # Force review for first N suggestions
  max_concurrent: 2                        # Max concurrent LLM requests

# Amount validation (SSOT)
amount_validation:
  require_positive: true                   # Amounts must be positive
  allow_sign_normalization: false          # Don't auto-flip negative amounts
  max_amount: 1000000.0                    # Sanity check maximum

# State database path
state_db_path: "data/state.db"

# Confidence thresholds
auto_threshold: 0.85   # Above this: auto-import
review_threshold: 0.60  # Above this: review, below: manual
"""

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        f.write(default_config)
