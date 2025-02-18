from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from snowflake.cli.api.commands.experimental_behaviour import (
    experimental_behaviour_enabled,
)
from snowflake.cli.api.feature_flags import FeatureFlag
from snowflake.cli.api.project.util import unquote_identifier
from snowflake.cli.api.sql_execution import SqlExecutionMixin
from snowflake.cli.plugins.connection.util import (
    MissingConnectionHostError,
    make_snowsight_url,
)
from snowflake.cli.plugins.object.stage.manager import StageManager
from snowflake.connector.cursor import SnowflakeCursor
from snowflake.connector.errors import ProgrammingError

log = logging.getLogger(__name__)


class StreamlitManager(SqlExecutionMixin):
    def share(self, streamlit_name: str, to_role: str) -> SnowflakeCursor:
        return self._execute_query(
            f"grant usage on streamlit {streamlit_name} to role {to_role}"
        )

    def _put_streamlit_files(
        self,
        root_location: str,
        main_file: Path,
        environment_file: Optional[Path],
        pages_dir: Optional[Path],
        additional_source_files: Optional[List[str]],
    ):
        stage_manager = StageManager()

        stage_manager.put(main_file, root_location, 4, True)

        if environment_file and environment_file.exists():
            stage_manager.put(environment_file, root_location, 4, True)

        if pages_dir and pages_dir.exists():
            stage_manager.put(pages_dir / "*.py", f"{root_location}/pages", 4, True)

        if additional_source_files:
            for file in additional_source_files:
                # If the file is in a folder, PUT it to the same folder in the stage
                # If not, just PUT it to the root of the stage
                destination = (
                    f"{root_location}/{str(Path(file).parent)}"
                    if "/" in file
                    else root_location
                )
                stage_manager.put(file, destination, 4, True)

    def _create_streamlit(
        self,
        fully_qualified_name: str,
        main_file: Path,
        replace: Optional[bool] = None,
        experimental: Optional[bool] = None,
        query_warehouse: Optional[str] = None,
        from_stage_name: Optional[str] = None,
    ):
        query = []
        if replace:
            query.append(f"CREATE OR REPLACE STREAMLIT {fully_qualified_name}")
        elif experimental:
            # For experimental behaviour, we need to use CREATE STREAMLIT IF NOT EXISTS
            # for a streamlit app with an embedded stage
            # because this is analogous to the behavior for non-experimental
            # deploy which does CREATE STAGE IF NOT EXISTS
            query.append(f"CREATE STREAMLIT IF NOT EXISTS {fully_qualified_name}")
        else:
            query.append(f"CREATE STREAMLIT {fully_qualified_name}")

        if from_stage_name:
            query.append(f"ROOT_LOCATION = '{from_stage_name}'")

        query.append(f"MAIN_FILE = '{main_file.name}'")

        if query_warehouse:
            query.append(f"QUERY_WAREHOUSE = {query_warehouse}")

        self._execute_query("\n".join(query))

    def deploy(
        self,
        streamlit_name: str,
        main_file: Path,
        environment_file: Optional[Path] = None,
        pages_dir: Optional[Path] = None,
        stage_name: Optional[str] = None,
        query_warehouse: Optional[str] = None,
        replace: Optional[bool] = False,
        additional_source_files: Optional[List[str]] = None,
        **options,
    ):
        stage_manager = StageManager()
        # for backwards compatibility - quoted stage path might be case-sensitive
        # https://docs.snowflake.com/en/sql-reference/identifiers-syntax#double-quoted-identifiers
        streamlit_name_for_root_location = self.get_name_from_fully_qualified_name(
            streamlit_name
        )
        fully_qualified_name = stage_manager.to_fully_qualified_name(streamlit_name)
        streamlit_name = self.get_name_from_fully_qualified_name(fully_qualified_name)
        if (
            experimental_behaviour_enabled()
            or FeatureFlag.ENABLE_STREAMLIT_EMBEDDED_STAGE.is_enabled()
        ):
            """
            1. Create streamlit object
            2. Upload files to embedded stage
            """
            # TODO: Support from_stage
            # from_stage_stmt = f"FROM_STAGE = '{stage_name}'" if stage_name else ""
            self._create_streamlit(
                fully_qualified_name,
                main_file,
                replace=replace,
                query_warehouse=query_warehouse,
                experimental=True,
            )
            try:
                self._execute_query(f"ALTER streamlit {fully_qualified_name} CHECKOUT")
            except ProgrammingError as e:
                # If an error is raised because a CHECKOUT has already occured,
                # simply skip it and continue
                if "Checkout already exists" in str(e):
                    log.info("Checkout already exists, continuing")
                else:
                    raise
            stage_path = stage_manager.to_fully_qualified_name(streamlit_name)
            embedded_stage_name = f"snow://streamlit/{stage_path}"
            root_location = f"{embedded_stage_name}/default_checkout"

            self._put_streamlit_files(
                root_location,
                main_file,
                environment_file,
                pages_dir,
                additional_source_files,
            )
        else:
            """
            1. Create stage
            2. Upload files to created stage
            3. Create streamlit from stage
            """
            stage_manager = StageManager()

            stage_name = stage_name or "streamlit"
            stage_name = stage_manager.to_fully_qualified_name(stage_name)

            stage_manager.create(stage_name=stage_name)

            root_location = stage_manager.get_standard_stage_prefix(
                f"{stage_name}/{streamlit_name_for_root_location}"
            )

            self._put_streamlit_files(
                root_location,
                main_file,
                environment_file,
                pages_dir,
                additional_source_files,
            )

            self._create_streamlit(
                fully_qualified_name,
                main_file,
                replace=replace,
                query_warehouse=query_warehouse,
                from_stage_name=root_location,
                experimental=False,
            )

        return self.get_url(fully_qualified_name)

    def get_url(self, streamlit_name: str, database=None, schema=None) -> str:
        try:
            return make_snowsight_url(
                self._conn,
                f"/#/streamlit-apps/{self.qualified_name_for_url(streamlit_name, database=database, schema=schema)}",
            )
        except MissingConnectionHostError as e:
            return "https://app.snowflake.com"

    def qualified_name_for_url(self, object_name: str, database=None, schema=None):
        fqn = self.to_fully_qualified_name(
            object_name, database=database, schema=schema
        )
        return ".".join(unquote_identifier(part) for part in fqn.split("."))
