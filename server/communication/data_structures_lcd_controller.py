"""Brew/Home LCD data structures shared by simulator protocol helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

LCD_CONTROLLER_BREW_SCHEMA_VERSION: int = 1
LCD_CONTROLLER_BREW_SCHEMA_NAME: str = "brew_home_v1"
LCD_CONTROLLER_MAX_PROFILES: int = 8
LCD_CONTROLLER_PROFILE_NAME_LENGTH: int = 32
LCD_CONTROLLER_BREW_VALID_SHOT_TIMER: int = 1 << 0
LCD_CONTROLLER_BREW_VALID_LIVE_PRESSURE: int = 1 << 1
LCD_CONTROLLER_BREW_VALID_WATER_LEVEL: int = 1 << 2
LCD_CONTROLLER_BREW_VALID_WEIGHT: int = 1 << 3
LCD_CONTROLLER_BREW_VALID_WARMUP: int = 1 << 4


@dataclass(slots=True)
class LCDControllerProfileSummary:
    """@brief Lightweight profile summary used by Brew/Home protocol bootstrap."""

    profile_id: int
    profile_name: str
    target_temperature_c: float
    target_pressure_bar: float
    target_flow_ml_s: float
    shot_target_g: float


@dataclass(slots=True)
class LCDControllerBrewHomeState:
    """@brief Runtime Brew/Home values mirrored into legacy DATA integer slots."""

    profile_id: int
    brew_elapsed_ms: int
    brew_duration_ms: int
    target_temperature_c: float
    target_pressure_bar: float
    target_flow_ml_s: float
    live_pressure_bar: float
    live_temperature_c: float
    live_water_level_pct: float
    live_weight_g: float
    shot_target_preview_g: float
    warmup_on: bool
    steam_on: bool
    uptime_minutes: float


def build_profile_summaries_from_presets(
    profile_presets: Sequence[dict[str, object]],
) -> list[LCDControllerProfileSummary]:
    """@brief Convert simulator profile presets to typed protocol summaries."""

    summaries: list[LCDControllerProfileSummary] = []

    for preset in profile_presets:
        profile_id = int(preset.get("id", len(summaries) + 1))
        profile_name = str(preset.get("name", f"Profile {profile_id}"))
        summaries.append(
            LCDControllerProfileSummary(
                profile_id=profile_id,
                profile_name=profile_name[:LCD_CONTROLLER_PROFILE_NAME_LENGTH],
                target_temperature_c=float(preset.get("target_temperature_c", 0.0)),
                target_pressure_bar=float(preset.get("target_pressure_bar", 0.0)),
                target_flow_ml_s=float(preset.get("target_flow_ml_sec", 0.0)),
                shot_target_g=float(preset.get("shot_target_g", 0.0)),
            )
        )

        if len(summaries) >= LCD_CONTROLLER_MAX_PROFILES:
            break

    return summaries


def brew_home_state_to_legacy_int_slots(state: LCDControllerBrewHomeState) -> list[int]:
    """@brief Encode Brew/Home state into the current 20-int legacy packet layout."""

    validity_mask = (
        LCD_CONTROLLER_BREW_VALID_SHOT_TIMER
        | LCD_CONTROLLER_BREW_VALID_LIVE_PRESSURE
        | LCD_CONTROLLER_BREW_VALID_WATER_LEVEL
        | LCD_CONTROLLER_BREW_VALID_WEIGHT
        | LCD_CONTROLLER_BREW_VALID_WARMUP
    )

    ints = [
        0,
        int(state.profile_id),
        int(max(0, state.brew_elapsed_ms)),
        int(max(0, state.brew_duration_ms)),
        int(round(float(state.target_pressure_bar) * 1000.0)),
        int(round(float(state.target_flow_ml_s) * 1000.0)),
        int(round(float(state.target_temperature_c) * 1000.0)),
        int(round(max(0.0, min(100.0, float(state.live_water_level_pct))) * 10.0)),
        int(round(max(0.0, min(200.0, float(state.live_weight_g))) * 100.0)),
        1 if state.warmup_on else 0,
        1 if state.steam_on else 0,
        int(round(max(0.0, min(100000.0, float(state.uptime_minutes))) * 10.0)),
        int(round(max(0.0, min(200.0, float(state.shot_target_preview_g))) * 100.0)),
        int(round(max(0.0, min(20.0, float(state.live_pressure_bar))) * 1000.0)),
        int(validity_mask),
    ]

    if len(ints) < 20:
        ints.extend([0] * (20 - len(ints)))
    else:
        ints = ints[:20]

    return ints
