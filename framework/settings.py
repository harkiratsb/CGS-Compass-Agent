"""Framework thresholds and defaults, loaded from settings.toml."""
import tomllib
from pathlib import Path
from typing import List

from pydantic import BaseModel, ValidationError, field_validator, model_validator

DEFAULT_PATH = Path(__file__).with_name("settings.toml")


class Gate(BaseModel):
    high: float
    medium: float

    @model_validator(mode="after")
    def _ordered(self):
        if self.high <= self.medium:
            raise ValueError("gate.high must be greater than gate.medium")
        return self


class Redesign(BaseModel):
    bands: List[int]

    @field_validator("bands")
    @classmethod
    def _four_ascending(cls, v):
        if len(v) != 4 or any(b <= a for a, b in zip(v, v[1:])):
            raise ValueError("redesign.bands must be 4 ascending numbers")
        return v


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


def _in_scale(model: BaseModel, where: str) -> None:
    for k, v in model:
        if not 1 <= v <= 5:
            raise ValueError(f"{where}.{k} must be between 1 and 5, got {v}")


def load_settings(path: Path | None = None) -> Settings:
    path = Path(path or DEFAULT_PATH)
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError as e:
        raise ValueError(f"settings file not found: {path}") from e
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"settings file {path} is not valid TOML: {e}") from e
    try:
        s = Settings.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"invalid settings in {path}: {e}") from e
    _in_scale(s.defaults, "defaults")
    _in_scale(s.readiness, "readiness")
    for name, group in s.tier:
        _in_scale(group, f"tier.{name}")
    return s
