from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Union

import tomlkit
from click import ClickException
from snowflake.cli.api.exceptions import (
    ConfigFileTooWidePermissionsError,
    MissingConfiguration,
    UnsupportedConfigSectionTypeError,
)
from snowflake.cli.api.secure_path import SecurePath
from snowflake.cli.api.secure_utils import file_permissions_are_strict
from snowflake.connector.compat import IS_WINDOWS
from snowflake.connector.config_manager import CONFIG_MANAGER
from snowflake.connector.constants import CONFIG_FILE, CONNECTIONS_FILE
from snowflake.connector.errors import MissingConfigOptionError
from tomlkit import TOMLDocument, dump
from tomlkit.container import Container
from tomlkit.exceptions import NonExistentKey
from tomlkit.items import Table

log = logging.getLogger(__name__)


class Empty:
    pass


CONNECTIONS_SECTION = "connections"
CLI_SECTION = "cli"
LOGS_SECTION = "logs"
PLUGINS_SECTION = "plugins"

LOGS_SECTION_PATH = [CLI_SECTION, LOGS_SECTION]
PLUGINS_SECTION_PATH = [CLI_SECTION, PLUGINS_SECTION]
FEATURE_FLAGS_SECTION_PATH = [CLI_SECTION, "features"]

CONFIG_MANAGER.add_option(
    name=CLI_SECTION,
    parse_str=tomlkit.parse,
    default=dict(),
)


@dataclass
class ConnectionConfig:
    account: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = field(default=None, repr=False)
    host: Optional[str] = None
    region: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    schema: Optional[str] = None
    warehouse: Optional[str] = None
    role: Optional[str] = None
    authenticator: Optional[str] = None
    private_key_path: Optional[str] = None

    _other_settings: dict = field(default_factory=lambda: {})

    @classmethod
    def from_dict(cls, config_dict: dict) -> ConnectionConfig:
        known_settings = {}
        other_settings = {}
        for key, value in config_dict.items():
            if key in cls.__dict__:
                known_settings[key] = value
            else:
                other_settings[key] = value
        return cls(**known_settings, _other_settings=other_settings)

    def to_dict_of_known_non_empty_values(self) -> dict:
        return {
            k: v
            for k, v in asdict(self).items()
            if k != "_other_settings" and v is not None
        }

    def _non_empty_other_values(self) -> dict:
        return {k: v for k, v in self._other_settings.items() if v is not None}

    def to_dict_of_all_non_empty_values(self) -> dict:
        return {
            **self.to_dict_of_known_non_empty_values(),
            **self._non_empty_other_values(),
        }


def config_init(config_file: Optional[Path]):
    """
    Initializes the app configuration. Config provided via cli flag takes precedence.
    If config file does not exist we create an empty one.
    """
    if config_file:
        CONFIG_MANAGER.file_path = config_file
    else:
        _check_default_config_files_permissions()
    if not CONFIG_MANAGER.file_path.exists():
        _initialise_config(CONFIG_MANAGER.file_path)
    CONFIG_MANAGER.read_config()


def add_connection(name: str, connection_config: ConnectionConfig):
    set_config_value(
        section=CONNECTIONS_SECTION,
        key=name,
        value=connection_config.to_dict_of_all_non_empty_values(),
    )


_DEFAULT_LOGS_CONFIG = {
    "save_logs": True,
    "path": str(CONFIG_MANAGER.file_path.parent / "logs"),
    "level": "info",
}

_DEFAULT_CLI_CONFIG = {LOGS_SECTION: _DEFAULT_LOGS_CONFIG}


@contextmanager
def _config_file():
    CONFIG_MANAGER.read_config()
    conf_file_cache = CONFIG_MANAGER.conf_file_cache
    yield conf_file_cache
    _dump_config(conf_file_cache)


def _initialise_logs_section():
    with _config_file() as conf_file_cache:
        if conf_file_cache.get(CLI_SECTION) is None:
            conf_file_cache[CLI_SECTION] = _DEFAULT_CLI_CONFIG
        if conf_file_cache[CLI_SECTION].get(LOGS_SECTION) is None:
            conf_file_cache[CLI_SECTION][LOGS_SECTION] = _DEFAULT_LOGS_CONFIG


def set_config_value(section: str | None, key: str, value: Any):
    with _config_file() as conf_file_cache:
        if section:
            if conf_file_cache.get(section) is None:
                conf_file_cache[section] = {}
            conf_file_cache[section][key] = value
        else:
            conf_file_cache[key] = value


def get_logs_config() -> dict:
    logs_config = _DEFAULT_LOGS_CONFIG.copy()
    if config_section_exists(*LOGS_SECTION_PATH):
        logs_config.update(**get_config_section(*LOGS_SECTION_PATH))
    return logs_config


def get_plugins_config() -> dict:
    if config_section_exists(*PLUGINS_SECTION_PATH):
        return get_config_section(*PLUGINS_SECTION_PATH)
    else:
        return {}


def connection_exists(connection_name: str) -> bool:
    return config_section_exists(CONNECTIONS_SECTION, connection_name)


def config_section_exists(*path) -> bool:
    try:
        _find_section(*path)
        return True
    except (KeyError, NonExistentKey, MissingConfigOptionError):
        return False


def get_all_connections() -> dict[str, ConnectionConfig]:
    return {
        k: ConnectionConfig.from_dict(connection_dict)
        for k, connection_dict in get_config_section("connections").items()
    }


def get_connection_dict(connection_name: str) -> dict:
    try:
        return get_config_section(CONNECTIONS_SECTION, connection_name)
    except KeyError:
        raise MissingConfiguration(f"Connection {connection_name} is not configured")


def get_default_connection_name() -> str:
    return CONFIG_MANAGER["default_connection_name"]


def get_default_connection_dict() -> dict:
    return get_connection_dict(get_default_connection_name())


def get_config_section(*path) -> dict:
    section = _find_section(*path)
    if isinstance(section, Container):
        return {s: _merge_section_with_env(section[s], *path, s) for s in section}
    if isinstance(section, dict):
        return _merge_section_with_env(section, *path)
    raise UnsupportedConfigSectionTypeError(type(section))


def get_config_value(*path, key: str, default: Optional[Any] = Empty) -> Any:
    """Looks for given key under nested path in toml file."""
    env_variable = get_env_value(*path, key=key)
    if env_variable:
        return env_variable
    try:
        return get_config_section(*path)[key]
    except (KeyError, NonExistentKey, MissingConfigOptionError):
        if default is not Empty:
            return default
        raise


def get_config_bool_value(*path, key: str, default: Optional[Any] = Empty) -> bool:
    value = get_config_value(*path, key=key, default=default)
    # If we get bool then we can return
    if isinstance(value, bool):
        return value

    # Now if value is not string then cast it to str. Simplifies logic for 1 and 0
    if not isinstance(value, str):
        value = str(value)

    know_booleans_mapping = {"true": True, "false": False, "1": True, "0": False}

    if value.lower() not in know_booleans_mapping:
        raise ClickException(
            f"Expected boolean value for {'.'.join((*path, key))} option."
        )
    return know_booleans_mapping[value.lower()]


def _initialise_config(config_file: Path) -> None:
    config_file = SecurePath(config_file)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.touch()
    _initialise_logs_section()
    log.info("Created Snowflake configuration file at %s", CONFIG_MANAGER.file_path)


def get_env_variable_name(*path, key: str) -> str:
    return "SNOWFLAKE_" + "_".join(p.upper() for p in path) + f"_{key.upper()}"


def get_env_value(*path, key: str) -> str | None:
    return os.environ.get(get_env_variable_name(*path, key=key))


def _find_section(*path) -> TOMLDocument:
    section = CONFIG_MANAGER
    idx = 0
    while idx < len(path):
        section = section[path[idx]]
        idx += 1
    return section


def _merge_section_with_env(section: Union[Table, Any], *path) -> Dict[str, str]:
    if isinstance(section, Table):
        env_variables = _get_envs_for_path(*path)
        section_copy = section.copy()
        section_copy.update(env_variables)
        return section_copy.unwrap()
    # It's a atomic value
    return section


def _get_envs_for_path(*path) -> dict:
    env_variables_prefix = "_".join(["SNOWFLAKE"] + [p.upper() for p in path]) + "_"
    return {
        k.replace(env_variables_prefix, "").lower(): os.environ[k]
        for k in os.environ.keys()
        if k.startswith(env_variables_prefix)
    }


def _dump_config(conf_file_cache: Dict):
    with SecurePath(CONFIG_MANAGER.file_path).open("w+") as fh:
        dump(conf_file_cache, fh)


def _check_default_config_files_permissions() -> None:
    if IS_WINDOWS:
        return
    if CONNECTIONS_FILE.exists() and not file_permissions_are_strict(CONNECTIONS_FILE):
        raise ConfigFileTooWidePermissionsError(CONNECTIONS_FILE)
    if CONFIG_FILE.exists() and not file_permissions_are_strict(CONFIG_FILE):
        raise ConfigFileTooWidePermissionsError(CONFIG_FILE)
