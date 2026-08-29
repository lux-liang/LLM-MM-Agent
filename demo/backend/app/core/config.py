"""
Configuration management using Pydantic Settings.

Centralizes all configuration from environment variables with sensible defaults.
"""
import secrets
import warnings
from typing import List, Union
from pydantic import AnyHttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Identity
    PROJECT_NAME: str = "MM-Agent Open Local Service"
    API_PREFIX: str = "/api/v1"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"
    OPEN_SOURCE_LOCAL_MODE: bool = True
    
    # Security
    # Empty by design: local development receives an ephemeral random key;
    # staging/production must provide a persistent SECRET_KEY via the env.
    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    ALGORITHM: str = "HS256"
    CORS_ORIGINS: List[Union[str, AnyHttpUrl]] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    
    # Local Auth Defaults
    ALLOW_PUBLIC_REGISTRATION: bool = False
    REQUIRE_INVITE_CODE: bool = False
    SEED_LOCAL_ADMIN: bool = False
    LOCAL_ADMIN_EMAIL: str = "admin@local.dev"
    LOCAL_ADMIN_PASSWORD: str = ""

    # Infrastructure
    DATABASE_URL: str = "sqlite+aiosqlite:///./runtime/mmagent.db"
    
    # Redis Configuration
    REDIS_URL: str = ""  # Optional. Leave empty for local single-process mode.
    REDIS_PREFIX: str = "lcp:v1"  # Namespace prefix for key isolation
    REDIS_STREAM_MAX_LEN: int = 2000  # Maximum length for Redis Streams
    
    # [FIX] Database Pool Tuning
    # Adjust based on Pod CPU/Worker count.
    # Default: 20 connections + 10 overflow. Recycle every hour.
    DB_POOL_SIZE: int = 50
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_RECYCLE: int = 3600

    STORAGE_ROOT: str = "runtime/storage/blobs"
    MAX_ASSET_DOWNLOAD_BYTES: int = 100 * 1024 * 1024
    TEMPLATE_ROOT: str = "app/templates"  # Relative to backend/ directory
    
    # External API Keys (Inject via Environment)
    OPENAI_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""
    E2B_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    TAVILY_API_KEY: str = ""
    
    # --- E2B Configuration (Architecture v3.0) ---
    # The alias to use for the custom environment
    E2B_TEMPLATE_ALIAS: str = "lcp-paper-agent-v3"
    # Base template to extend (Official E2B Code Interpreter)
    E2B_BASE_TEMPLATE: str = "code-interpreter-v1"
    # Metadata key used to tag sandboxes with project IDs for stateless discovery
    E2B_PROJECT_LABEL_KEY: str = "project_id"
    # Python packages to pre-install during build
    E2B_PIP_PACKAGES: List[str] = [
        "pandas",
        "numpy",
        "scipy",
        "scikit-learn",
        "xgboost",
        "lightgbm",
        "statsmodels",
        "patsy",
        "sympy",
        "networkx",
        "cvxpy",
        "ortools",
        "matplotlib",
        "seaborn",
        "plotly",
        "openpyxl",
        "tabulate",
        "pyarrow",
        "requests",
        "beautifulsoup4",
        "tqdm",
        "joblib",
    ]
    # APT packages to pre-install during build (LaTeX toolchain)
    E2B_APT_PACKAGES: List[str] = [
        "texlive-latex-base",
        "texlive-latex-extra",
        "texlive-xetex",
        "texlive-science",
        "texlive-fonts-recommended",
        "latexmk",
        "ripgrep",
        "git",
        "curl",
        "vim",
        "ghostscript",
    ]
    # Node/NPM packages to pre-install during build (for claude-code CLI)
    E2B_NPM_PACKAGES: List[str] = [
        "@anthropic-ai/claude-code@latest",
        "@musistudio/claude-code-router",  # Router middleware for LLM routing
        "csv-parser"
    ]
    # Set to True to force a rebuild on every restart (useful for dev, costly for prod)
    E2B_FORCE_REBUILD: bool = False
    
    # Custom LLM Config (from .env)
    API_KEY: str = ""      # OpenAI-compatible API key for router / direct calls
    BASE_URL: str = "https://api.openai.com/v1"
    MODEL_NAME: str = "gpt-5.6-sol"
    AGENT_MODEL_NAME: str = "" # 将被 Router 映射的目标模型 (默认使用 MODEL_NAME)
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    REASONING_EFFORT: str = "high"
    
    # Router Configuration Toggle
    # 如果为 True，SandboxGateway 将启动 Router 并劫持 claude 请求
    USE_LLM_ROUTER: bool = True
    # Claude Code's dangerous permission bypass is opt-in and should remain
    # disabled for shared/production deployments.
    ALLOW_DANGEROUS_CLAUDE_PERMISSIONS: bool = False
    
    # [REFACTORED] Infrastructure Retries (Tenacity - Network Layer)
    # These are for API-level retries (500/429 errors), NOT business logic retries.
    # MUST BE PRESERVED - Critical for network stability.
    LLM_RETRY_ATTEMPTS: int = 3
    LLM_RETRY_MIN_WAIT: int = 1
    LLM_RETRY_MAX_WAIT: int = 3
    
    # [REFACTORED] Agent Resource Boundaries
    # Replaces explicit retry counts (AUTO_FIX_RETRIES, COMPILATION_RETRIES).
    # The Agent manages its own loops within this time budget.
    DEFAULT_AGENT_TIMEOUT: int = 3600
    
    # Constraints (Legacy - kept for backward compatibility)
    DEFAULT_TIMEOUT: int = 3600  # Seconds
    
    # Sandbox Lifecycle
    SANDBOX_TIMEOUT: int = 3600
    SANDBOX_EXECUTION_TIMEOUT: int = 3600
    # Remote working directory
    SANDBOX_DATA_DIR: str = "/home/user/workdir"
    SANDBOX_CONNECTION_RETRIES: int = 3

    # System Buffers
    # [FIX] Separate buffers to prevent log flooding from evicting critical state
    EVENT_BUS_HISTORY_SIZE: int = 10        # Critical events (State, Errors)
    EVENT_BUS_LOG_HISTORY_SIZE: int = 50  # High-volume logs (EXEC_LOG)

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)
    
    @model_validator(mode="after")
    def set_agent_model_default(self):
        """
        If AGENT_MODEL_NAME is not explicitly set (empty string), 
        default it to MODEL_NAME to ensure compatibility with the provider.
        This fixes the issue where SiliconFlow doesn't support Claude models.
        """
        if not self.AGENT_MODEL_NAME:
            self.AGENT_MODEL_NAME = self.MODEL_NAME
        if not self.SECRET_KEY:
            if self.ENVIRONMENT.lower() in {"production", "prod", "staging"}:
                raise ValueError(
                    "SECRET_KEY must be set explicitly in staging/production"
                )
            self.SECRET_KEY = secrets.token_urlsafe(48)
            warnings.warn(
                "SECRET_KEY is not set; generated an ephemeral local key. "
                "Set SECRET_KEY in .env for persistent sessions.",
                RuntimeWarning,
                stacklevel=2,
            )
        if self.ENVIRONMENT.lower() in {"production", "prod", "staging"}:
            if "*" in [str(origin) for origin in self.CORS_ORIGINS]:
                raise ValueError("wildcard CORS is forbidden outside development")
            if self.ALLOW_PUBLIC_REGISTRATION and not self.REQUIRE_INVITE_CODE:
                raise ValueError(
                    "public registration requires REQUIRE_INVITE_CODE outside development"
                )
            if self.SEED_LOCAL_ADMIN:
                raise ValueError("SEED_LOCAL_ADMIN must be disabled outside development")
        if self.SEED_LOCAL_ADMIN and not self.LOCAL_ADMIN_PASSWORD:
            raise ValueError(
                "LOCAL_ADMIN_PASSWORD is required when SEED_LOCAL_ADMIN is enabled"
            )
        return self

    model_config = SettingsConfigDict(
        env_file=[".env", "backend/.env", "../.env"], 
        extra="ignore",
        env_file_encoding="utf-8"
    )


settings = Settings()
