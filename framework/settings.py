"""Framework thresholds and defaults, loaded from settings.toml."""
import tomllib
from pathlib import Path
from typing import List

from pydantic import BaseModel, ValidationError

DEFAULT_PATH = Path(__file__).with_name("settings.toml")


class Gate(BaseModel):
    high: float
    medium: float


class Redesign(BaseModel):
    bands: List[int]


class Defaults(BaseModel):
    dial: int
    rule_clarity: int
    readiness: int


class TierRules(BaseModel):
    clarity_min: int
    input_min: int
    variation_max: int
    judgment_max: int


class TierAgent(BaseModel):
    checkability_min: int
    judgment_min: int
    input_max: int
    variation_min: int
    approval_stakes_min: int


class TierHuman(BaseModel):
    stakes: int
    judgment_min: int
    checkability_max: int


class Tier(BaseModel):
    rules: TierRules
    agent: TierAgent
    human: TierHuman


class Readiness(BaseModel):
    ready_min: int


class Loop(BaseModel):
    extra_passes: int


class Settings(BaseModel):
    gate: Gate
    redesign: Redesign
    defaults: Defaults
    tier: Tier
    readiness: Readiness
    loop: Loop


def load_settings(path: Path | None = None) -> Settings:
    with open(path or DEFAULT_PATH, "rb") as f:
        data = tomllib.load(f)
    try:
        return Settings.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"invalid settings: {e}") from e
