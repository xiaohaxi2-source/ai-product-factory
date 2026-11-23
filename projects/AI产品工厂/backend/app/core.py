import logging
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

STAGE = '2-4'
LOCAL_OWNER = 'local'
PRODUCT_ROOT = Path(__file__).resolve().parents[2]
KIT_ROOT = PRODUCT_ROOT.parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(Path(__file__).resolve().parents[1] / '.env'),
                                      env_file_encoding='utf-8', extra='ignore')
    model_api_key: str = ''
    model_base_url: str = 'https://api.deepseek.com'
    model_backend: str = 'codex'
    model_name: str = ''
    model_fallback_name: str = 'deepseek-flash'
    model_timeout: float = 120.0
    development_timeout: float = 300.0
    model_max_retries: int = 1
    codex_bin: str = 'codex'
    codex_effort: str = 'low'
    codex_use_system_proxy: bool = True
    max_calls: int = 60
    total_budget_rmb: float = 50.0
    input_rmb_per_million: float = 2.0
    output_rmb_per_million: float = 8.0
    port: int = 8124
    data_dir: str = str(PRODUCT_ROOT / 'data')
    projects_dir: str = ''


def get_settings() -> Settings:
    return Settings()


class Problem(Exception):
    """统一业务错误：API 层转成 {"error":{"code","message}}。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
