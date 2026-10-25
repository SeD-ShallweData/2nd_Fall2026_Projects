"""Private Seoul credentials, independent of the LLM configuration."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import re
from llm_config import read_env_file

@dataclass(frozen=True)
class SeoulConfig:
    api_key: str = field(repr=False)
    source: str = 'UNCONFIGURED'
    error: str | None = None

def load_seoul_config(environ=None,home=None):
    env=dict(os.environ if environ is None else environ)
    try:
        if 'SEOUL_OPEN_DATA_API_KEY' in env:
            key=env['SEOUL_OPEN_DATA_API_KEY'].strip();source='PROCESS_ENV'
        else:
            explicit=env.get('SEOUL_ENV_FILE')
            path=Path(explicit).expanduser() if explicit else Path(home or Path.home())/'Desktop/서울열린데이터광장 API KEY.env'
            if not path.is_file() and not explicit:return SeoulConfig('')
            if not path.is_file() or path.stat().st_size>65536:raise ValueError()
            raw=path.read_text(encoding='utf-8-sig').strip()
            if re.fullmatch(r'[A-Za-z0-9_-]{12,256}',raw):key=raw
            else:
                values=read_env_file(path)
                if set(values)!={'SEOUL_OPEN_DATA_API_KEY'}:raise ValueError()
                key=values['SEOUL_OPEN_DATA_API_KEY'].strip()
            source='EXPLICIT_SEOUL_FILE' if explicit else 'INITIAL_SEOUL_DESKTOP_FILE'
        if key and not re.fullmatch(r'[A-Za-z0-9_-]{12,256}',key):raise ValueError()
        return SeoulConfig(key,source)
    except (ValueError,OSError,UnicodeError,TypeError):
        return SeoulConfig('','CONFIG_ERROR','SEOUL_CONFIGURATION_INVALID')
