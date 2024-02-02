from __future__ import annotations

import logging
from datetime import datetime

import pytest
from snowflake.cli.api.cli_global_context import cli_context_manager
from snowflake.cli.api.commands.decorators import global_options, with_output
from snowflake.cli.api.config import config_init
from snowflake.cli.api.console import cli_console
from snowflake.cli.api.output.types import QueryResult
from snowflake.cli.app import loggers
from snowflake.cli.app.cli_app import app


pytest_plugins = ["tests.testing_utils.fixtures", "tests.project.fixtures"]


@pytest.fixture(autouse=True)
# Global context and logging levels reset is required.
# Without it, state from previous tests is visible in following tests.
#
# This automatically used setup fixture is required to use test.conf from resources
# in unit tests which are not using "runner" fixture (tests which do not invoke CLI command).
def reset_global_context_and_setup_config_and_logging_levels(
    request, test_snowcli_config
):
    cli_context_manager.reset()
    cli_context_manager.set_verbose(False)
    cli_context_manager.set_enable_tracebacks(False)
    config_init(test_snowcli_config)
    loggers.create_loggers(verbose=False, debug=False)
    yield


# This automatically used cleanup fixture is required to avoid random breaking of logging
# in one test caused by presence of capsys in other test.
# See similar issues: https://github.com/pytest-dev/pytest/issues/5502
@pytest.fixture(autouse=True)
def clean_logging_handlers_fixture(request):
    yield
    clean_logging_handlers()


# This automatically used fixture isolates default location
# of config files from user's system.
@pytest.fixture(autouse=True)
def isolate_snowflake_home(snowflake_home):
    yield snowflake_home


def clean_logging_handlers():
    for logger in [logging.getLogger()] + list(
        logging.Logger.manager.loggerDict.values()
    ):
        handlers = [hdl for hdl in getattr(logger, "handlers", [])]
        for handler in handlers:
            logger.removeHandler(handler)


@pytest.fixture(name="_create_mock_cursor")
def make_mock_cursor(mock_cursor):
    return lambda: mock_cursor(
        columns=["string", "number", "array", "object", "date"],
        rows=[
            ("string", 42, ["array"], {"k": "object"}, datetime(2022, 3, 21)),
            ("string", 43, ["array"], {"k": "object"}, datetime(2022, 3, 21)),
        ],
    )


@pytest.fixture(name="faker_app")
def make_faker_app(_create_mock_cursor):
    @app.command("Faker")
    @with_output
    @global_options
    def faker_app(**options):
        """Faker app"""
        with cli_console.phase("Enter", "Exit") as step:
            step("Faker. Teeny Tiny step: UNO UNO")

        return QueryResult(_create_mock_cursor())

    yield
