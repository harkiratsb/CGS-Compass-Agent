"""Save and open a whole working session as one JSON project file."""
from __future__ import annotations

import base64
import binascii

from pydantic import BaseModel, ValidationError

from ingest.parse import ParsedFile, parse_file
from schema import AgentRun, ProcessModel

PROJECT_VERSION = 1


class ProjectError(Exception):
    """A project file could not be opened; the message is safe to show the user."""


class StoredFile(BaseModel):
    name: str
    data_b64: str


class Project(BaseModel):
    version: int
    files: list[StoredFile]
    intake: dict[str, str]
    model: ProcessModel
    run: AgentRun | None = None
    mode: str | None = None
    map_reviewed: bool = False
    framework: dict | None = None  # a dumped FrameworkRun, typed loosely here


def save_project(files, intake, model, run, mode, map_reviewed, framework) -> str:
    proj = Project(
        version=PROJECT_VERSION,
        files=[StoredFile(name=n, data_b64=base64.b64encode(b).decode("ascii")) for n, b in files],
        intake={k: str(v) for k, v in intake.items()},
        model=model,
        run=run,
        mode=mode,
        map_reviewed=map_reviewed,
        framework=framework,
    )
    return proj.model_dump_json(indent=2)


def load_project(text: str) -> tuple[Project, list[tuple[str, bytes]]]:
    try:
        proj = Project.model_validate_json(text)
    except ValidationError:
        import json
        try:
            version = json.loads(text).get("version")
        except (ValueError, AttributeError):
            version = None
        if isinstance(version, int) and version != PROJECT_VERSION:
            raise ProjectError(f"This project was saved by a different version of the app (version {version}).")
        raise ProjectError("This file is not a valid project file.") from None
    if proj.version != PROJECT_VERSION:
        raise ProjectError(f"This project was saved by a different version of the app (version {proj.version}).")
    try:
        files = [(f.name, base64.b64decode(f.data_b64, validate=True)) for f in proj.files]
    except (binascii.Error, ValueError):
        raise ProjectError("This project file is damaged: an attached file could not be read.") from None
    return proj, files


def parsed_files(files) -> list[ParsedFile]:
    return [parse_file(n, b) for n, b in files]
