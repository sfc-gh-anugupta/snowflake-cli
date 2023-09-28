from __future__ import annotations

import functools
import json
import tempfile
import shutil

import pytest
from dataclasses import dataclass
from pathlib import Path
from snowcli.app.cli_app import app
from typer import Typer
from typer.testing import CliRunner
from typing import List, Dict, Any, Optional

TEST_DIR = Path(__file__).parent
DEFAULT_TEST_CONFIG = "connection_configs.toml"


@dataclass
class CommandResult:
    exit_code: int
    json: Optional[List[Dict[str, Any]] | Dict[str, Any]] = None
    output: Optional[str] = None


class TestConfigProvider:
    def __init__(self, temp_dir_with_configs: Path):
        self._temp_dir_with_configs = temp_dir_with_configs

    def get_config_path(self, file_name: str) -> Path:
        return self._temp_dir_with_configs / file_name


@pytest.fixture(scope="session")
def test_snowcli_config_provider():
    with tempfile.TemporaryDirectory() as td:
        temp_dst = Path(td) / "config"
        shutil.copytree(TEST_DIR / "config", temp_dst)
        yield TestConfigProvider(temp_dst)


@pytest.fixture(scope="session")
def test_root_path():
    return TEST_DIR


class SnowCLIRunner(CliRunner):
    def __init__(self, app: Typer, test_config_provider: TestConfigProvider):
        super().__init__()
        self.app = app
        self._test_config_provider = test_config_provider
        self._test_config_path = self._test_config_provider.get_config_path(
            DEFAULT_TEST_CONFIG
        )

    def use_config(self, config_file_name: str) -> None:
        self._test_config_path = self._test_config_provider.get_config_path(
            config_file_name
        )

    @functools.wraps(CliRunner.invoke)
    def _invoke(self, *a, **kw):
        kw.update(catch_exceptions=False)
        return super().invoke(self.app, *a, **kw)

    def invoke_with_config(self, *args, **kwargs) -> CommandResult:
        result = self._invoke(
            ["--config-file", self._test_config_path, *args[0]],
            **kwargs,
        )
        return CommandResult(result.exit_code, output=result.output)

    def invoke_integration(self, *args, **kwargs) -> CommandResult:
        result = self._invoke(
            [
                "--config-file",
                self._test_config_path,
                *args[0],
                "--format",
                "JSON",
                "-c",
                "integration",
            ],
            **kwargs,
        )
        if result.output == "" or result.output.strip() == "Done":
            return CommandResult(result.exit_code, json=[])
        return CommandResult(result.exit_code, json.loads(result.output))

    def invoke_integration_without_format(self, *args, **kwargs) -> CommandResult:
        result = self._invoke(
            [
                "--config-file",
                self._test_config_path,
                *args[0],
                "-c",
                "integration",
            ],
            **kwargs,
        )
        return CommandResult(result.exit_code, output=result.output)


@pytest.fixture
def runner(test_snowcli_config_provider):
    return SnowCLIRunner(app, test_snowcli_config_provider)
