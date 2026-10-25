"""서버 전용 설정. 명시적 환경 > 지정 env > 제한적 초기 파일 탐색 > 공개 기본값."""
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
import os
import re


class ConfigError(ValueError):
    pass


def read_env_file(path):
    """dotenv의 할당문만 읽는다. 코드/보간/쉘 명령은 실행하지 않는다."""
    p = Path(path).expanduser()
    if not p.is_file() or p.stat().st_size > 65536:
        raise ConfigError("ENV_FILE_MISSING_OR_TOO_LARGE")
    try:
        text = p.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise ConfigError("ENV_FILE_UNREADABLE") from None
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not m or m[1] in result:
            raise ConfigError("ENV_FILE_INVALID_OR_DUPLICATE_ASSIGNMENT")
        value = m[2].strip()
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigError("ENV_FILE_UNBALANCED_QUOTES")
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        if any(ord(c) < 32 for c in value):
            raise ConfigError("ENV_FILE_CONTROL_CHARACTER")
        result[m[1]] = value
    return result


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    model: str
    base_url: str
    enabled: bool
    api_key: str = field(repr=False)
    source: str = "DEFAULTS"
    key_variable: str = "UPSTAGE_API_KEY"
    timeout: int = 20
    max_tokens: int = 1800
    max_rounds: int = 4
    max_tools: int = 8
    error: str | None = None


def load_llm_config(environ=None, home=None):
    env = dict(os.environ if environ is None else environ)
    file_values, source = {}, "PROCESS_ENV"
    explicit = env.get("LLM_ENV_FILE")
    try:
        if explicit:
            file_values, source = read_env_file(explicit), "EXPLICIT_ENV_FILE"
        elif not any(env.get(k) for k in ("UPSTAGE_API_KEY", "Upstage_API_KEY", "LLM_API_KEY", env.get("LLM_API_KEY_VAR", "UPSTAGE_API_KEY"))):
            # 초기 설치에서만 사용하는 제한적 호환 탐색. 경로 교체는 LLM_ENV_FILE로 한다.
            initial = Path(home or Path.home()) / "Desktop" / "Upstage API KEY.env"
            if initial.is_file():
                file_values, source = read_env_file(initial), "INITIAL_DESKTOP_FILE"
        def pick(key, default=""):
            return env[key] if key in env else file_values.get(key, default)
        aliases = [pick("LLM_API_KEY_VAR", "UPSTAGE_API_KEY"), "UPSTAGE_API_KEY", "Upstage_API_KEY", "LLM_API_KEY"]
        key, key_name = "", aliases[0]
        # 키도 설정 계층 우선순위가 별칭 우선순위보다 우선한다. 명시적 빈값은 파일로 대체하지 않는다.
        for layer in (env, file_values):
            found = next((name for name in aliases if name in layer), None)
            if found is not None:
                key, key_name = layer[found].strip(), found
                break
        provider = pick("LLM_PROVIDER", "upstage").lower()
        model = pick("LLM_MODEL", "solar-pro4")
        base = pick("LLM_BASE_URL", "https://api.upstage.ai/v1").rstrip("/")
        url = urlsplit(base)
        hosts = {x.strip().lower() for x in pick("LLM_ALLOWED_HOSTS", "api.upstage.ai").split(",") if x.strip()}
        if provider not in ("upstage", "openai_compatible"):
            raise ConfigError("UNSUPPORTED_LLM_PROVIDER")
        if not re.fullmatch(r"[a-zA-Z0-9_.:/\-]{1,100}", model):
            raise ConfigError("INVALID_LLM_MODEL")
        if url.scheme != "https" or not url.hostname or url.hostname.lower() not in hosts or url.username or url.password or url.query or url.fragment or url.port not in (None, 443):
            raise ConfigError("LLM_ENDPOINT_NOT_ALLOWED_HTTPS")
        if not isinstance(key, str) or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ConfigError("INVALID_LLM_KEY_FORMAT")
        enable = pick("ROA_ENABLE_LLM", "0")
        if enable not in ("0", "1"):
            raise ConfigError("INVALID_LLM_ENABLE_FLAG")
        timeout, tokens = int(pick("LLM_TIMEOUT_SECONDS", "20")), int(pick("LLM_MAX_TOKENS", "1800"))
        if not 1 <= timeout <= 45 or not 100 <= tokens <= 4000:
            raise ConfigError("LLM_LIMITS_OUT_OF_RANGE")
        return LlmConfig(provider, model, base, enable == "1", key, source, key_name, timeout, tokens)
    except (ConfigError, ValueError, OSError):
        # 파일 내용·경로·비밀값은 예외/공개 상태에 넣지 않는다.
        return LlmConfig("upstage", "", "https://api.upstage.ai/v1", False, "", "CONFIG_ERROR", error="LLM_CONFIGURATION_INVALID")
