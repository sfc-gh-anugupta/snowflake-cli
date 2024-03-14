from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import typer
from snowflake.cli.api import secure_path
from snowflake.cli.api.commands.decorators import global_options
from snowflake.cli.api.commands.flags import DEFAULT_CONTEXT_SETTINGS
from snowflake.cli.api.utils.rendering import jinja_render_from_file

app = typer.Typer(context_settings=DEFAULT_CONTEXT_SETTINGS, hidden=True, name="render")


def _parse_key_value(key_value_str: str):
    parts = key_value_str.split("=")
    if len(parts) < 2:
        raise ValueError("Passed key-value pair does not comform with key=value format")

    return parts[0], "=".join(parts[1:])


@app.command("template")
@global_options
def render_template(
    template_path: Path = typer.Argument(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to template file",
    ),
    data_file_path: Optional[Path] = typer.Option(
        None,
        "--data-file",
        "-d",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to JSON file with data that will be passed to the template",
    ),
    data_override: Optional[List[str]] = typer.Option(
        None,
        "--data",
        "-D",
        help="String in format of key=value that will be passed to rendered template. "
        "If used together with data file then this will override existing values",
    ),
    output_file_path: Optional[Path] = typer.Option(
        None,
        "--output-file",
        "-o",
        dir_okay=False,
        help="If provided then rendered template will be written to this file",
    ),
    **options,
):
    """Renders Jinja2 template. Can be used to construct complex SQL files."""
    data = {}
    if data_file_path:
        data = json.loads(
            secure_path.SecurePath(data_file_path).read_text(
                file_size_limit_mb=secure_path.UNLIMITED
            )
        )
    if data_override:
        for key_value_str in data_override:
            key, value = _parse_key_value(key_value_str)
            data[key] = value

    jinja_render_from_file(
        template_path=template_path, data=data, output_file_path=output_file_path
    )
