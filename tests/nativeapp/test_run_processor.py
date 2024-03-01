import os
from textwrap import dedent
from unittest import mock

import pytest
import typer
from click import UsageError
from snowflake.cli.api.project.definition_manager import DefinitionManager
from snowflake.cli.plugins.nativeapp.constants import (
    LOOSE_FILES_MAGIC_VERSION,
    SPECIAL_COMMENT,
)
from snowflake.cli.plugins.nativeapp.exceptions import (
    ApplicationAlreadyExistsError,
    ApplicationPackageAlreadyExistsError,
    ApplicationPackageDoesNotExistError,
    UnexpectedOwnerError,
)
from snowflake.cli.plugins.nativeapp.policy import (
    AllowAlwaysPolicy,
    AskAlwaysPolicy,
    DenyAlwaysPolicy,
)
from snowflake.cli.plugins.nativeapp.run_processor import NativeAppRunProcessor
from snowflake.cli.plugins.object.stage.diff import DiffResult
from snowflake.connector import ProgrammingError
from snowflake.connector.cursor import DictCursor

from src.snowflake.cli.plugins.nativeapp.constants import SPECIAL_COMMENT_OLD
from tests.nativeapp.patch_utils import (
    mock_connection,
    mock_get_app_pkg_distribution_in_sf,
)
from tests.nativeapp.utils import (
    NATIVEAPP_MANAGER_EXECUTE,
    NATIVEAPP_MANAGER_EXECUTE_QUERIES,
    NATIVEAPP_MANAGER_IS_APP_PKG_DISTRIBUTION_SAME,
    RUN_MODULE,
    RUN_PROCESSOR_GET_EXISTING_APP_INFO,
    RUN_PROCESSOR_GET_EXISTING_APP_PKG_INFO,
    TYPER_CONFIRM,
    mock_execute_helper,
    mock_snowflake_yml_file,
    quoted_override_yml_file,
)
from tests.testing_utils.files_and_dirs import create_named_file
from tests.testing_utils.fixtures import MockConnectionCtx

mock_project_definition_override = {
    "native_app": {
        "application": {
            "name": "sample_application_name",
            "role": "sample_application_role",
        },
        "package": {
            "name": "sample_package_name",
            "role": "sample_package_role",
        },
    }
}

allow_always_policy = AllowAlwaysPolicy()
ask_always_policy = AskAlwaysPolicy()
deny_always_policy = DenyAlwaysPolicy()


def _get_na_run_processor():
    dm = DefinitionManager()
    return NativeAppRunProcessor(
        project_definition=dm.project_definition["native_app"],
        project_root=dm.project_root,
    )


# Test create_app_package() with no existing package available
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_PKG_INFO, return_value=None)
def test_create_app_pkg_no_existing_package(
    mock_get_existing_app_pkg_info, mock_execute, temp_dir, mock_cursor
):
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role package_role")),
            (
                None,
                mock.call(
                    dedent(
                        f"""\
                        create application package app_pkg
                            comment = {SPECIAL_COMMENT}
                            distribution = internal
                    """
                    )
                ),
            ),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_execute.side_effect = side_effects

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    run_processor.create_app_package()
    assert mock_execute.mock_calls == expected
    mock_get_existing_app_pkg_info.assert_called_once()


# Test create_app_package() with incorrect owner
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_PKG_INFO)
def test_create_app_pkg_incorrect_owner(mock_get_existing_app_pkg_info, temp_dir):
    mock_get_existing_app_pkg_info.return_value = {
        "name": "APP_PKG",
        "comment": SPECIAL_COMMENT,
        "version": LOOSE_FILES_MAGIC_VERSION,
        "owner": "wrong_owner",
    }

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    with pytest.raises(UnexpectedOwnerError):
        run_processor = _get_na_run_processor()
        run_processor.create_app_package()


# Test create_app_package() with distribution external AND variable mismatch
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_PKG_INFO)
@mock_get_app_pkg_distribution_in_sf()
@mock.patch(NATIVEAPP_MANAGER_IS_APP_PKG_DISTRIBUTION_SAME)
@mock.patch(f"{RUN_MODULE}.cc.warning")
@pytest.mark.parametrize(
    "is_pkg_distribution_same",
    [False, True],
)
def test_create_app_pkg_external_distribution(
    mock_warning,
    mock_is_distribution_same,
    mock_get_distribution,
    mock_get_existing_app_pkg_info,
    is_pkg_distribution_same,
    temp_dir,
):
    mock_is_distribution_same.return_value = is_pkg_distribution_same
    mock_get_distribution.return_value = "external"
    mock_get_existing_app_pkg_info.return_value = {
        "name": "APP_PKG",
        "comment": "random",
        "version": LOOSE_FILES_MAGIC_VERSION,
        "owner": "PACKAGE_ROLE",
    }

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    run_processor.create_app_package()
    if not is_pkg_distribution_same:
        mock_warning.assert_called_once_with(
            "Continuing to execute `snow app run` on application package app_pkg with distribution 'external'."
        )


# Test create_app_package() with distribution internal AND variable mismatch AND special comment is True
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_PKG_INFO)
@mock_get_app_pkg_distribution_in_sf()
@mock.patch(NATIVEAPP_MANAGER_IS_APP_PKG_DISTRIBUTION_SAME)
@mock.patch(f"{RUN_MODULE}.cc.warning")
@pytest.mark.parametrize(
    "is_pkg_distribution_same, special_comment",
    [
        (False, SPECIAL_COMMENT),
        (False, SPECIAL_COMMENT_OLD),
        (True, SPECIAL_COMMENT),
        (True, SPECIAL_COMMENT_OLD),
    ],
)
def test_create_app_pkg_internal_distribution_special_comment(
    mock_warning,
    mock_is_distribution_same,
    mock_get_distribution,
    mock_get_existing_app_pkg_info,
    is_pkg_distribution_same,
    special_comment,
    temp_dir,
):
    mock_is_distribution_same.return_value = is_pkg_distribution_same
    mock_get_distribution.return_value = "internal"
    mock_get_existing_app_pkg_info.return_value = {
        "name": "APP_PKG",
        "comment": special_comment,
        "version": LOOSE_FILES_MAGIC_VERSION,
        "owner": "PACKAGE_ROLE",
    }

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    run_processor.create_app_package()
    if not is_pkg_distribution_same:
        mock_warning.assert_called_once_with(
            "Continuing to execute `snow app run` on application package app_pkg with distribution 'internal'."
        )


# Test create_app_package() with distribution internal AND variable mismatch AND special comment is False
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_PKG_INFO)
@mock_get_app_pkg_distribution_in_sf()
@mock.patch(NATIVEAPP_MANAGER_IS_APP_PKG_DISTRIBUTION_SAME)
@mock.patch(f"{RUN_MODULE}.cc.warning")
@pytest.mark.parametrize(
    "is_pkg_distribution_same",
    [False, True],
)
def test_create_app_pkg_internal_distribution_no_special_comment(
    mock_warning,
    mock_is_distribution_same,
    mock_get_distribution,
    mock_get_existing_app_pkg_info,
    is_pkg_distribution_same,
    temp_dir,
):
    mock_is_distribution_same.return_value = is_pkg_distribution_same
    mock_get_distribution.return_value = "internal"
    mock_get_existing_app_pkg_info.return_value = {
        "name": "APP_PKG",
        "comment": "dummy",
        "version": LOOSE_FILES_MAGIC_VERSION,
        "owner": "PACKAGE_ROLE",
    }

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    with pytest.raises(ApplicationPackageAlreadyExistsError):
        run_processor.create_app_package()

    if not is_pkg_distribution_same:
        mock_warning.assert_called_once_with(
            "Continuing to execute `snow app run` on application package app_pkg with distribution 'internal'."
        )


# Test create_dev_app with exception thrown trying to use the warehouse
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock_connection()
def test_create_dev_app_w_warehouse_access_exception(
    mock_conn, mock_execute, temp_dir, mock_cursor
):
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (
                ProgrammingError(
                    msg="Object does not exist, or operation cannot be performed.",
                    errno=2043,
                ),
                mock.call("use warehouse app_warehouse"),
            ),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    mock_diff_result = DiffResult()
    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    assert not mock_diff_result.has_changes()

    with pytest.raises(ProgrammingError) as err:
        run_processor._create_dev_app(mock_diff_result)  # noqa: SLF001

    assert mock_execute.mock_calls == expected
    assert "Please grant usage privilege on warehouse to this role." in err.value.msg


# Test create_dev_app with no existing application AND create succeeds AND app role == package role
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO, return_value=None)
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock_connection()
def test_create_dev_app_create_new_w_no_additional_privileges(
    mock_conn, mock_execute, mock_get_existing_app_info, temp_dir, mock_cursor
):
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (
                None,
                mock.call(
                    dedent(
                        f"""\
                    create application myapp
                        from application package app_pkg
                        using @app_pkg.app_src.stage
                        debug_mode = True
                        comment = {SPECIAL_COMMENT}
                    """
                    )
                ),
            ),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    mock_diff_result = DiffResult()
    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file.replace("package_role", "app_role")],
    )

    run_processor = _get_na_run_processor()
    assert not mock_diff_result.has_changes()
    run_processor._create_dev_app(mock_diff_result)  # noqa: SLF001
    assert mock_execute.mock_calls == expected


# Test create_dev_app with no existing application AND create succeeds AND app role != package role
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO, return_value=None)
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock.patch(NATIVEAPP_MANAGER_EXECUTE_QUERIES)
@mock_connection()
def test_create_dev_app_create_new_with_additional_privileges(
    mock_conn,
    mock_execute_queries,
    mock_execute_query,
    mock_get_existing_app_info,
    temp_dir,
    mock_cursor,
):
    side_effects, mock_execute_query_expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (
                mock_cursor([{"CURRENT_ROLE()": "app_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role package_role")),
            (None, mock.call("use role app_role")),
            (
                None,
                mock.call(
                    dedent(
                        f"""\
                    create application myapp
                        from application package app_pkg
                        using @app_pkg.app_src.stage
                        debug_mode = True
                        comment = {SPECIAL_COMMENT}
                    """
                    )
                ),
            ),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute_query.side_effect = side_effects

    mock_execute_queries_expected = [
        mock.call(
            dedent(
                f"""\
            grant install, develop on application package app_pkg to role app_role;
            grant usage on schema app_pkg.app_src to role app_role;
            grant read on stage app_pkg.app_src.stage to role app_role;
            """
            )
        )
    ]
    mock_execute_queries.side_effect = [None, None, None]

    mock_diff_result = DiffResult()
    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    assert not mock_diff_result.has_changes()
    run_processor._create_dev_app(mock_diff_result)  # noqa: SLF001
    assert mock_execute_query.mock_calls == mock_execute_query_expected
    assert mock_execute_queries.mock_calls == mock_execute_queries_expected


# Test create_dev_app with no existing application AND create throws an exception
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO, return_value=None)
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock_connection()
def test_create_dev_app_create_new_w_missing_warehouse_exception(
    mock_conn, mock_execute, mock_get_existing_app_info, temp_dir, mock_cursor
):
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (
                ProgrammingError(
                    msg="No active warehouse selected in the current session", errno=606
                ),
                mock.call(
                    dedent(
                        f"""\
                    create application myapp
                        from application package app_pkg
                        using @app_pkg.app_src.stage
                        debug_mode = True
                        comment = {SPECIAL_COMMENT}
                    """
                    )
                ),
            ),
            (None, mock.call("use role old_role")),
        ]
    )

    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    mock_diff_result = DiffResult()
    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file.replace("package_role", "app_role")],
    )

    run_processor = _get_na_run_processor()
    assert not mock_diff_result.has_changes()

    with pytest.raises(ProgrammingError) as err:
        run_processor._create_dev_app(mock_diff_result)  # noqa: SLF001

    assert "Please provide a warehouse for the active session role" in err.value.msg
    assert mock_execute.mock_calls == expected


# Test create_dev_app with existing application AND bad comment AND good version
# Test create_dev_app with existing application AND bad comment AND bad version
# Test create_dev_app with existing application AND good comment(s) AND bad version
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO)
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock_connection()
@pytest.mark.parametrize(
    "comment, version",
    [
        ("dummy", LOOSE_FILES_MAGIC_VERSION),
        ("dummy", "dummy"),
        (SPECIAL_COMMENT, "dummy"),
        (SPECIAL_COMMENT_OLD, "dummy"),
    ],
)
def test_create_dev_app_incorrect_properties(
    mock_conn,
    mock_execute,
    mock_get_existing_app_info,
    comment,
    version,
    temp_dir,
    mock_cursor,
):
    mock_get_existing_app_info.return_value = {
        "name": "MYAPP",
        "comment": comment,
        "version": version,
        "owner": "APP_ROLE",
    }
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    mock_diff_result = DiffResult()
    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    with pytest.raises(ApplicationAlreadyExistsError):
        run_processor = _get_na_run_processor()
        assert not mock_diff_result.has_changes()
        run_processor._create_dev_app(mock_diff_result)  # noqa: SLF001

    assert mock_execute.mock_calls == expected


# Test create_dev_app with existing application AND incorrect owner
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO)
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock_connection()
def test_create_dev_app_incorrect_owner(
    mock_conn, mock_execute, mock_get_existing_app_info, temp_dir, mock_cursor
):
    mock_get_existing_app_info.return_value = {
        "name": "MYAPP",
        "comment": SPECIAL_COMMENT,
        "version": LOOSE_FILES_MAGIC_VERSION,
        "owner": "accountadmin_or_something",
    }
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    mock_diff_result = DiffResult()
    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    with pytest.raises(UnexpectedOwnerError):
        run_processor = _get_na_run_processor()
        assert not mock_diff_result.has_changes()
        run_processor._create_dev_app(mock_diff_result)  # noqa: SLF001

    assert mock_execute.mock_calls == expected


# Test create_dev_app with existing application AND diff has no changes
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO)
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock_connection()
def test_create_dev_app_no_diff_changes(
    mock_conn, mock_execute, mock_get_existing_app_info, temp_dir, mock_cursor
):
    mock_get_existing_app_info.return_value = {
        "name": "MYAPP",
        "comment": SPECIAL_COMMENT,
        "version": LOOSE_FILES_MAGIC_VERSION,
        "owner": "APP_ROLE",
    }
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (None, mock.call("alter application myapp set debug_mode = True")),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    mock_diff_result = DiffResult()
    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    assert not mock_diff_result.has_changes()
    run_processor._create_dev_app(mock_diff_result)  # noqa: SLF001
    assert mock_execute.mock_calls == expected


# Test create_dev_app with existing application AND diff has changes
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO)
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock_connection()
def test_create_dev_app_w_diff_changes(
    mock_conn, mock_execute, mock_get_existing_app_info, temp_dir, mock_cursor
):
    mock_get_existing_app_info.return_value = {
        "name": "MYAPP",
        "comment": SPECIAL_COMMENT,
        "version": LOOSE_FILES_MAGIC_VERSION,
        "owner": "APP_ROLE",
    }
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (
                None,
                mock.call(
                    "alter application myapp upgrade using @app_pkg.app_src.stage"
                ),
            ),
            (None, mock.call("alter application myapp set debug_mode = True")),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    mock_diff_result = DiffResult(different=["setup.sql"])
    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    assert mock_diff_result.has_changes()
    run_processor._create_dev_app(mock_diff_result)  # noqa: SLF001
    assert mock_execute.mock_calls == expected


# Test create_dev_app with existing application AND alter throws an error
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO)
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock_connection()
def test_create_dev_app_recreate_w_missing_warehouse_exception(
    mock_conn, mock_execute, mock_get_existing_app_info, temp_dir, mock_cursor
):
    mock_get_existing_app_info.return_value = {
        "name": "MYAPP",
        "comment": SPECIAL_COMMENT,
        "version": LOOSE_FILES_MAGIC_VERSION,
        "owner": "APP_ROLE",
    }
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (
                ProgrammingError(
                    msg="No active warehouse selected in the current session", errno=606
                ),
                mock.call(
                    "alter application myapp upgrade using @app_pkg.app_src.stage"
                ),
            ),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    mock_diff_result = DiffResult(different=["setup.sql"])
    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    assert mock_diff_result.has_changes()

    with pytest.raises(ProgrammingError) as err:
        run_processor._create_dev_app(mock_diff_result)  # noqa: SLF001

    assert mock_execute.mock_calls == expected
    assert "Please provide a warehouse for the active session role" in err.value.msg


# Test create_dev_app with no existing application AND quoted name scenario 1
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO, return_value=None)
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock_connection()
def test_create_dev_app_create_new_quoted(
    mock_conn, mock_execute, mock_get_existing_app_info, temp_dir, mock_cursor
):
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (
                None,
                mock.call(
                    dedent(
                        f"""\
                    create application "My Application"
                        from application package "My Package"
                        using '@"My Package".app_src.stage'
                        debug_mode = True
                        comment = {SPECIAL_COMMENT}
                    """
                    )
                ),
            ),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    mock_diff_result = DiffResult()
    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[
            dedent(
                """\
            definition_version: 1
            native_app:
                name: '"My Native Application"'

                source_stage:
                    app_src.stage

                artifacts:
                - setup.sql
                - app/README.md
                - src: app/streamlit/*.py
                dest: ui/

                application:
                    name: >-
                        "My Application"
                    role: app_role
                    warehouse: app_warehouse
                    debug: true

                package:
                    name: >-
                        "My Package"
                    role: app_role
                    scripts:
                    - shared_content.sql
        """
            )
        ],
    )

    run_processor = _get_na_run_processor()
    assert not mock_diff_result.has_changes()
    run_processor._create_dev_app(mock_diff_result)  # noqa: SLF001
    assert mock_execute.mock_calls == expected


# Test create_dev_app with no existing application AND quoted name scenario 2
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO, return_value=None)
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock_connection()
def test_create_dev_app_create_new_quoted_override(
    mock_conn, mock_execute, mock_get_existing_app_info, temp_dir, mock_cursor
):
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (
                None,
                mock.call(
                    dedent(
                        f"""\
                    create application "My Application"
                        from application package "My Package"
                        using '@"My Package".app_src.stage'
                        debug_mode = True
                        comment = {SPECIAL_COMMENT}
                    """
                    )
                ),
            ),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    mock_diff_result = DiffResult()
    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file.replace("package_role", "app_role")],
    )
    create_named_file(
        file_name="snowflake.local.yml",
        dir_name=current_working_directory,
        contents=[quoted_override_yml_file],
    )

    run_processor = _get_na_run_processor()
    assert not mock_diff_result.has_changes()
    run_processor._create_dev_app(mock_diff_result)  # noqa: SLF001
    assert mock_execute.mock_calls == expected


# Test upgrade app method for release directives AND throws warehouse error
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock_connection()
@pytest.mark.parametrize(
    "policy_param", [allow_always_policy, ask_always_policy, deny_always_policy]
)
def test_upgrade_app_warehouse_error(
    mock_conn, mock_execute, policy_param, temp_dir, mock_cursor
):
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (
                ProgrammingError(
                    msg="Object does not exist, or operation cannot be performed.",
                    errno=2043,
                ),
                mock.call("use warehouse app_warehouse"),
            ),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    with pytest.raises(ProgrammingError):
        run_processor.upgrade_app(policy_param, is_interactive=True)
    assert mock_execute.mock_calls == expected


# Test upgrade app method for release directives AND existing app info AND bad owner
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO)
@mock_connection()
@pytest.mark.parametrize(
    "policy_param", [allow_always_policy, ask_always_policy, deny_always_policy]
)
def test_upgrade_app_incorrect_owner(
    mock_conn,
    mock_get_existing_app_info,
    mock_execute,
    policy_param,
    temp_dir,
    mock_cursor,
):
    mock_get_existing_app_info.return_value = {
        "name": "APP",
        "comment": SPECIAL_COMMENT,
        "owner": "wrong_owner",
    }
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    with pytest.raises(UnexpectedOwnerError):
        run_processor.upgrade_app(policy=policy_param, is_interactive=True)
    assert mock_execute.mock_calls == expected


# Test upgrade app method for release directives AND existing app info AND upgrade succeeds
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO)
@mock_connection()
@pytest.mark.parametrize(
    "policy_param", [allow_always_policy, ask_always_policy, deny_always_policy]
)
def test_upgrade_app_succeeds(
    mock_conn,
    mock_get_existing_app_info,
    mock_execute,
    policy_param,
    temp_dir,
    mock_cursor,
):
    mock_get_existing_app_info.return_value = {
        "name": "myapp",
        "comment": SPECIAL_COMMENT,
        "owner": "app_role",
    }
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (None, mock.call("alter application myapp upgrade ")),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    run_processor.upgrade_app(policy=policy_param, is_interactive=True)
    assert mock_execute.mock_calls == expected


# Test upgrade app method for release directives AND existing app info AND upgrade fails due to generic error
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO)
@mock_connection()
@pytest.mark.parametrize(
    "policy_param", [allow_always_policy, ask_always_policy, deny_always_policy]
)
def test_upgrade_app_fails_generic_error(
    mock_conn,
    mock_get_existing_app_info,
    mock_execute,
    policy_param,
    temp_dir,
    mock_cursor,
):
    mock_get_existing_app_info.return_value = {
        "name": "myapp",
        "comment": SPECIAL_COMMENT,
        "owner": "app_role",
    }
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (
                ProgrammingError(
                    msg="Some Error Message.",
                    errno=1234,
                ),
                mock.call("alter application myapp upgrade "),
            ),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    with pytest.raises(ProgrammingError):
        run_processor.upgrade_app(policy=policy_param, is_interactive=True)
    assert mock_execute.mock_calls == expected


# Test upgrade app method for release directives AND existing app info AND upgrade fails due to upgrade restriction error AND --force is False AND interactive mode is False AND --interactive is False
# Test upgrade app method for release directives AND existing app info AND upgrade fails due to upgrade restriction error AND --force is False AND interactive mode is False AND --interactive is True AND  user does not want to proceed
# Test upgrade app method for release directives AND existing app info AND upgrade fails due to upgrade restriction error AND --force is False AND interactive mode is True AND user does not want to proceed
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO)
@mock.patch(
    f"snowflake.cli.plugins.nativeapp.policy.{TYPER_CONFIRM}", return_value=False
)
@mock_connection()
@pytest.mark.parametrize(
    "policy_param, is_interactive_param, expected_code",
    [(deny_always_policy, False, 1), (ask_always_policy, True, 0)],
)
def test_upgrade_app_fails_upgrade_restriction_error(
    mock_conn,
    mock_typer_confirm,
    mock_get_existing_app_info,
    mock_execute,
    policy_param,
    is_interactive_param,
    expected_code,
    temp_dir,
    mock_cursor,
):
    mock_get_existing_app_info.return_value = {
        "name": "myapp",
        "comment": SPECIAL_COMMENT,
        "owner": "app_role",
    }
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (
                ProgrammingError(
                    msg="Some Error Message.",
                    errno=93044,
                ),
                mock.call("alter application myapp upgrade "),
            ),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    with pytest.raises(typer.Exit):
        result = run_processor.upgrade_app(
            policy_param, is_interactive=is_interactive_param
        )
        assert result.exit_code == expected_code
    assert mock_execute.mock_calls == expected


# Test upgrade app method for release directives AND existing app info AND upgrade fails due to upgrade restriction error AND --force is True AND drop fails
# Test upgrade app method for release directives AND existing app info AND upgrade fails due to upgrade restriction error AND --force is False AND interactive mode is False AND --interactive is True AND user wants to proceed AND drop fails
# Test upgrade app method for release directives AND existing app info AND upgrade fails due to upgrade restriction error AND --force is False AND interactive mode is True AND user wants to proceed AND drop fails
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO)
@mock.patch(
    f"snowflake.cli.plugins.nativeapp.policy.{TYPER_CONFIRM}", return_value=True
)
@mock_connection()
@pytest.mark.parametrize(
    "policy_param, is_interactive_param",
    [(allow_always_policy, False), (ask_always_policy, True)],
)
def test_upgrade_app_fails_drop_fails(
    mock_conn,
    mock_typer_confirm,
    mock_get_existing_app_info,
    mock_execute,
    policy_param,
    is_interactive_param,
    temp_dir,
    mock_cursor,
):
    mock_get_existing_app_info.return_value = {
        "name": "myapp",
        "comment": SPECIAL_COMMENT,
        "owner": "app_role",
    }
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (
                ProgrammingError(
                    msg="Some Error Message.",
                    errno=93044,
                ),
                mock.call("alter application myapp upgrade "),
            ),
            (
                ProgrammingError(
                    msg="Some Error Message.",
                    errno=1234,
                ),
                mock.call("drop application myapp"),
            ),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    with pytest.raises(ProgrammingError):
        run_processor.upgrade_app(policy_param, is_interactive=is_interactive_param)
    assert mock_execute.mock_calls == expected


# Test upgrade app method for release directives AND existing app info AND user wants to drop app AND drop succeeds AND app is created successfully.
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO)
@mock.patch(
    f"snowflake.cli.plugins.nativeapp.policy.{TYPER_CONFIRM}", return_value=True
)
@mock_connection()
@pytest.mark.parametrize("policy_param", [allow_always_policy, ask_always_policy])
def test_upgrade_app_recreate_app(
    mock_conn,
    mock_typer_confirm,
    mock_get_existing_app_info,
    mock_execute,
    policy_param,
    temp_dir,
    mock_cursor,
):
    mock_get_existing_app_info.return_value = {
        "name": "myapp",
        "comment": SPECIAL_COMMENT,
        "owner": "app_role",
    }
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (
                ProgrammingError(
                    msg="Some Error Message.",
                    errno=93044,
                ),
                mock.call("alter application myapp upgrade "),
            ),
            (None, mock.call("drop application myapp")),
            (
                mock_cursor([{"CURRENT_ROLE()": "app_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role package_role")),
            (
                None,
                mock.call(
                    "grant install on application package app_pkg to role app_role"
                ),
            ),
            (None, mock.call("use role app_role")),
            (
                None,
                mock.call(
                    dedent(
                        f"""\
            create application myapp
                from application package app_pkg 
                comment = {SPECIAL_COMMENT}
            """
                    )
                ),
            ),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    run_processor.upgrade_app(policy_param, is_interactive=True)
    assert mock_execute.mock_calls == expected


# Test upgrade app method for version AND no existing version info
@mock.patch(
    "snowflake.cli.plugins.nativeapp.run_processor.NativeAppRunProcessor.get_existing_version_info",
    return_value=None,
)
@pytest.mark.parametrize(
    "policy_param", [allow_always_policy, ask_always_policy, deny_always_policy]
)
def test_upgrade_app_from_version_throws_usage_error_one(
    mock_existing, policy_param, temp_dir
):

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    with pytest.raises(UsageError):
        run_processor.process(policy=policy_param, version="v1", is_interactive=True)


# Test upgrade app method for version AND no existing app package from version info
@mock.patch(
    "snowflake.cli.plugins.nativeapp.run_processor.NativeAppRunProcessor.get_existing_version_info",
    side_effect=ApplicationPackageDoesNotExistError("app_pkg"),
)
@pytest.mark.parametrize(
    "policy_param", [allow_always_policy, ask_always_policy, deny_always_policy]
)
def test_upgrade_app_from_version_throws_usage_error_two(
    mock_existing, policy_param, temp_dir
):

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    with pytest.raises(UsageError):
        run_processor.process(policy=policy_param, version="v1", is_interactive=True)


# Test upgrade app method for version AND existing app info AND user wants to drop app AND drop succeeds AND app is created successfully
@mock.patch(
    "snowflake.cli.plugins.nativeapp.run_processor.NativeAppRunProcessor.get_existing_version_info",
    return_value={"key": "val"},
)
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
@mock.patch(RUN_PROCESSOR_GET_EXISTING_APP_INFO)
@mock.patch(
    f"snowflake.cli.plugins.nativeapp.policy.{TYPER_CONFIRM}", return_value=True
)
@mock_connection()
@pytest.mark.parametrize("policy_param", [allow_always_policy, ask_always_policy])
def test_upgrade_app_recreate_app_from_version(
    mock_conn,
    mock_typer_confirm,
    mock_get_existing_app_info,
    mock_execute,
    mock_existing,
    policy_param,
    temp_dir,
    mock_cursor,
):
    mock_get_existing_app_info.return_value = {
        "name": "myapp",
        "comment": SPECIAL_COMMENT,
        "owner": "app_role",
    }
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role app_role")),
            (None, mock.call("use warehouse app_warehouse")),
            (
                ProgrammingError(
                    msg="Some Error Message.",
                    errno=93044,
                ),
                mock.call("alter application myapp upgrade using version v1 "),
            ),
            (None, mock.call("drop application myapp")),
            (
                mock_cursor([{"CURRENT_ROLE()": "app_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role package_role")),
            (
                None,
                mock.call(
                    "grant install on application package app_pkg to role app_role"
                ),
            ),
            (
                None,
                mock.call(
                    "grant develop on application package app_pkg to role app_role"
                ),
            ),
            (None, mock.call("use role app_role")),
            (
                None,
                mock.call(
                    dedent(
                        f"""\
            create application myapp
                from application package app_pkg using version v1 
                comment = {SPECIAL_COMMENT}
            """
                    )
                ),
            ),
            (None, mock.call("alter application myapp set debug_mode = True")),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_conn.return_value = MockConnectionCtx()
    mock_execute.side_effect = side_effects

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    run_processor = _get_na_run_processor()
    run_processor.process(policy=policy_param, version="v1", is_interactive=True)
    assert mock_execute.mock_calls == expected


# Test get_existing_version_info returns version info correctly
@mock.patch(NATIVEAPP_MANAGER_EXECUTE)
def test_get_existing_version_info(mock_execute, temp_dir, mock_cursor):
    version = "V1"
    side_effects, expected = mock_execute_helper(
        [
            (
                mock_cursor([{"CURRENT_ROLE()": "old_role"}], []),
                mock.call("select current_role()", cursor_class=DictCursor),
            ),
            (None, mock.call("use role package_role")),
            (
                mock_cursor(
                    [
                        {
                            "name": "My Package",
                            "comment": "some comment",
                            "owner": "PACKAGE_ROLE",
                            "version": version,
                        }
                    ],
                    [],
                ),
                mock.call(
                    f"show versions like 'V1' in application package app_pkg",
                    cursor_class=DictCursor,
                ),
            ),
            (None, mock.call("use role old_role")),
        ]
    )
    mock_execute.side_effect = side_effects

    current_working_directory = os.getcwd()
    create_named_file(
        file_name="snowflake.yml",
        dir_name=current_working_directory,
        contents=[mock_snowflake_yml_file],
    )

    processor = _get_na_run_processor()
    result = processor.get_existing_version_info(version)
    assert mock_execute.mock_calls == expected
    assert result["version"] == version
