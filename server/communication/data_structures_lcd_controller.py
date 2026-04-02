"""Brew/Home LCD data structures shared by simulator protocol helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

LCD_CONTROLLER_DATA_SCHEMA_VERSION: int = 12
LCD_CONTROLLER_BREW_SCHEMA_VERSION: int = 1
LCD_CONTROLLER_BREW_SCHEMA_NAME: str = "brew_home_v1"
LCD_CONTROLLER_MAX_PROFILES: int = 5
LCD_CONTROLLER_PROFILE_NAME_LENGTH: int = 25
LCD_CONTROLLER_BREW_VALID_SHOT_TIMER: int = 1 << 0
LCD_CONTROLLER_BREW_VALID_LIVE_PRESSURE: int = 1 << 1
LCD_CONTROLLER_BREW_VALID_WATER_LEVEL: int = 1 << 2
LCD_CONTROLLER_BREW_VALID_WEIGHT: int = 1 << 3
LCD_CONTROLLER_BREW_VALID_WARMUP: int = 1 << 4

LCD_CONTROLLER_DATASET_SETTINGS_FIELD_ORDER: tuple[str, ...] = (
    "steam_setpoint",
    "offset_temp",
    "hpwr",
    "main_divider",
    "brew_divider",
    "active_profile",
    "power_line_frequency",
    "lcd_sleep",
    "warmup_state",
    "home_on_shot_finish",
    "brew_delta_state",
    "basket_prefill",
    "scales_f1",
    "scales_f2",
    "pump_flow_at_zero",
    "led_state",
    "led_disco",
    "led_r",
    "led_g",
    "led_b",
)

LCD_CONTROLLER_DATASET_PROFILE_FIELD_ORDER: tuple[str, ...] = (
    "name",
    "preinfusion_state",
    "preinfusion_flow_state",
    "preinfusion_sec",
    "preinfusion_bar",
    "preinfusion_flow_vol",
    "preinfusion_flow_time",
    "preinfusion_flow_pressure_target",
    "preinfusion_pressure_flow_target",
    "preinfusion_filled",
    "preinfusion_pressure_above",
    "preinfusion_weight_above",
    "soak_state",
    "soak_time_pressure",
    "soak_time_flow",
    "soak_keep_pressure",
    "soak_keep_flow",
    "soak_below_pressure",
    "soak_above_pressure",
    "soak_above_weight",
    "preinfusion_ramp",
    "preinfusion_ramp_slope",
    "tp_state",
    "tp_type",
    "tp_profiling_start",
    "tp_profiling_finish",
    "tp_profiling_hold",
    "tp_profiling_hold_limit",
    "tp_profiling_slope",
    "tp_profiling_slope_shape",
    "tp_profiling_flow_restriction",
    "tf_profile_start",
    "tf_profile_end",
    "tf_profile_hold",
    "tf_profile_hold_limit",
    "tf_profile_slope",
    "tf_profile_slope_shape",
    "tf_profiling_pressure_restriction",
    "profiling_state",
    "mf_profile_state",
    "mp_profiling_start",
    "mp_profiling_finish",
    "mp_profiling_slope",
    "mp_profiling_slope_shape",
    "mp_profiling_flow_restriction",
    "mf_profile_start",
    "mf_profile_end",
    "mf_profile_slope",
    "mf_profile_slope_shape",
    "mf_profiling_pressure_restriction",
    "setpoint",
    "stop_on_weight_state",
    "shot_dose",
    "shot_stop_on_custom_weight",
    "shot_preset",
)

LCD_CONTROLLER_DATASET_PROFILE_BOOL_FIELDS: frozenset[str] = frozenset(
    {
        "preinfusion_state",
        "preinfusion_flow_state",
        "soak_state",
        "tp_state",
        "tp_type",
        "profiling_state",
        "mf_profile_state",
        "stop_on_weight_state",
    }
)

LCD_CONTROLLER_DATASET_SETTINGS_BOOL_FIELDS: frozenset[str] = frozenset(
    {
        "warmup_state",
        "home_on_shot_finish",
        "brew_delta_state",
        "basket_prefill",
        "led_state",
        "led_disco",
    }
)

LCD_CONTROLLER_DATASET_PROFILE_FLOAT_FIELDS: frozenset[str] = frozenset(
    {
        "preinfusion_bar",
        "preinfusion_flow_vol",
        "preinfusion_flow_pressure_target",
        "preinfusion_pressure_flow_target",
        "preinfusion_filled",
        "preinfusion_pressure_above",
        "preinfusion_weight_above",
        "soak_keep_pressure",
        "soak_keep_flow",
        "soak_below_pressure",
        "soak_above_pressure",
        "soak_above_weight",
        "tp_profiling_start",
        "tp_profiling_finish",
        "tp_profiling_hold_limit",
        "tp_profiling_flow_restriction",
        "tf_profile_start",
        "tf_profile_end",
        "tf_profile_hold_limit",
        "tf_profiling_pressure_restriction",
        "mp_profiling_start",
        "mp_profiling_finish",
        "mp_profiling_flow_restriction",
        "mf_profile_start",
        "mf_profile_end",
        "mf_profiling_pressure_restriction",
        "shot_dose",
        "shot_stop_on_custom_weight",
    }
)

LCD_CONTROLLER_DATASET_SETTINGS_FLOAT_FIELDS: frozenset[str] = frozenset({"pump_flow_at_zero"})


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


def _normalize_bool(raw_value: object, default_value: bool = False) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return bool(int(raw_value))
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        return normalized in {"1", "true", "yes", "on"}
    return default_value


def _normalize_int(raw_value: object, default_value: int = 0) -> int:
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return int(default_value)


def _normalize_float(raw_value: object, default_value: float = 0.0) -> float:
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return float(default_value)


def _normalize_name(raw_value: object, fallback_name: str) -> str:
    normalized_name = str(raw_value) if raw_value is not None else fallback_name
    if not normalized_name:
        normalized_name = fallback_name
    return normalized_name[:LCD_CONTROLLER_PROFILE_NAME_LENGTH]


def _default_profile(index: int) -> dict[str, object]:
    profile_id = index + 1
    return {
        "id": profile_id,
        "name": _normalize_name(f"Profile {profile_id}", f"Profile {profile_id}"),
        "preinfusion_state": True,
        "preinfusion_flow_state": False,
        "preinfusion_sec": 0,
        "preinfusion_bar": 0.0,
        "preinfusion_flow_vol": 0.0,
        "preinfusion_flow_time": 0,
        "preinfusion_flow_pressure_target": 0.0,
        "preinfusion_pressure_flow_target": 0.0,
        "preinfusion_filled": 0.0,
        "preinfusion_pressure_above": False,
        "preinfusion_weight_above": 0.0,
        "soak_state": False,
        "soak_time_pressure": 0,
        "soak_time_flow": 0,
        "soak_keep_pressure": 0.0,
        "soak_keep_flow": 0.0,
        "soak_below_pressure": 0.0,
        "soak_above_pressure": 0.0,
        "soak_above_weight": 0.0,
        "preinfusion_ramp": 0,
        "preinfusion_ramp_slope": 0,
        "tp_state": True,
        "tp_type": False,
        "tp_profiling_start": 0.0,
        "tp_profiling_finish": 0.0,
        "tp_profiling_hold": 0,
        "tp_profiling_hold_limit": 0.0,
        "tp_profiling_slope": 0,
        "tp_profiling_slope_shape": 0,
        "tp_profiling_flow_restriction": 0.0,
        "tf_profile_start": 0.0,
        "tf_profile_end": 0.0,
        "tf_profile_hold": 0,
        "tf_profile_hold_limit": 0.0,
        "tf_profile_slope": 0,
        "tf_profile_slope_shape": 0,
        "tf_profiling_pressure_restriction": 0.0,
        "profiling_state": True,
        "mf_profile_state": True,
        "mp_profiling_start": 0.0,
        "mp_profiling_finish": 0.0,
        "mp_profiling_slope": 0,
        "mp_profiling_slope_shape": 0,
        "mp_profiling_flow_restriction": 0.0,
        "mf_profile_start": 0.0,
        "mf_profile_end": 0.0,
        "mf_profile_slope": 0,
        "mf_profile_slope_shape": 0,
        "mf_profiling_pressure_restriction": 0.0,
        "setpoint": 930,
        "stop_on_weight_state": True,
        "shot_dose": 18.0,
        "shot_stop_on_custom_weight": 36.0,
        "shot_preset": 0,
    }


def _default_settings() -> dict[str, object]:
    return {
        "steam_setpoint": 1450,
        "offset_temp": 0,
        "hpwr": 1000,
        "main_divider": 100,
        "brew_divider": 100,
        "active_profile": 1,
        "power_line_frequency": 50,
        "lcd_sleep": 30,
        "warmup_state": True,
        "home_on_shot_finish": False,
        "brew_delta_state": False,
        "basket_prefill": False,
        "scales_f1": 1,
        "scales_f2": 1,
        "pump_flow_at_zero": 0.0,
        "led_state": True,
        "led_disco": False,
        "led_r": 255,
        "led_g": 190,
        "led_b": 120,
    }


def normalize_lcd_profile(profile: Mapping[str, object], index: int) -> dict[str, object]:
    normalized = _default_profile(index)
    normalized["id"] = max(1, _normalize_int(profile.get("id", normalized["id"]), normalized["id"]))
    normalized["name"] = _normalize_name(profile.get("name", normalized["name"]), f"Profile {normalized['id']}")

    for key in LCD_CONTROLLER_DATASET_PROFILE_FIELD_ORDER:
        if key == "name":
            normalized[key] = _normalize_name(profile.get(key, normalized[key]), normalized["name"])
        elif key in LCD_CONTROLLER_DATASET_PROFILE_BOOL_FIELDS:
            normalized[key] = _normalize_bool(profile.get(key, normalized[key]), bool(normalized[key]))
        elif key in LCD_CONTROLLER_DATASET_PROFILE_FLOAT_FIELDS:
            normalized[key] = _normalize_float(profile.get(key, normalized[key]), float(normalized[key]))
        else:
            normalized[key] = _normalize_int(profile.get(key, normalized[key]), int(normalized[key]))

    return normalized


def normalize_lcd_settings(settings: Mapping[str, object] | None) -> dict[str, object]:
    normalized = _default_settings()
    source = settings or {}
    for key in LCD_CONTROLLER_DATASET_SETTINGS_FIELD_ORDER:
        if key in LCD_CONTROLLER_DATASET_SETTINGS_BOOL_FIELDS:
            normalized[key] = _normalize_bool(source.get(key, normalized[key]), bool(normalized[key]))
        elif key in LCD_CONTROLLER_DATASET_SETTINGS_FLOAT_FIELDS:
            normalized[key] = _normalize_float(source.get(key, normalized[key]), float(normalized[key]))
        else:
            normalized[key] = _normalize_int(source.get(key, normalized[key]), int(normalized[key]))
    normalized["active_profile"] = max(
        1,
        min(
            LCD_CONTROLLER_MAX_PROFILES,
            int(normalized["active_profile"]),
        ),
    )
    return normalized


def build_lcd_dataset_from_presets(
    profile_presets: Sequence[dict[str, object]],
    settings: Mapping[str, object] | None = None,
    *,
    schema_version: int = LCD_CONTROLLER_DATA_SCHEMA_VERSION,
) -> dict[str, object]:
    profiles: list[dict[str, object]] = []
    for index in range(LCD_CONTROLLER_MAX_PROFILES):
        if index < len(profile_presets):
            source_profile = profile_presets[index]
        else:
            source_profile = _default_profile(index)
        profiles.append(normalize_lcd_profile(source_profile, index))

    return {
        "schema_version": int(schema_version),
        "settings": normalize_lcd_settings(settings),
        "profiles": profiles,
    }


def _format_payload_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def encode_lcd_dataset_payload(dataset: Mapping[str, object]) -> str:
    schema_version = _normalize_int(dataset.get("schema_version", LCD_CONTROLLER_DATA_SCHEMA_VERSION),
                                    LCD_CONTROLLER_DATA_SCHEMA_VERSION)
    settings_source = dataset.get("settings")
    settings = normalize_lcd_settings(settings_source if isinstance(settings_source, Mapping) else None)
    profiles_source = dataset.get("profiles")
    profile_sequence = profiles_source if isinstance(profiles_source, list) else []
    profiles = build_lcd_dataset_from_presets(
        [profile for profile in profile_sequence if isinstance(profile, dict)],
        settings=settings,
        schema_version=schema_version,
    )["profiles"]

    settings_csv = ",".join(
        _format_payload_scalar(settings[field_name])
        for field_name in LCD_CONTROLLER_DATASET_SETTINGS_FIELD_ORDER
    )
    profile_rows: list[str] = []
    for profile in profiles:
        fields = [_format_payload_scalar(profile.get("id", 1))]
        fields.extend(
            _format_payload_scalar(profile.get(field_name, ""))
            for field_name in LCD_CONTROLLER_DATASET_PROFILE_FIELD_ORDER
        )
        profile_rows.append(",".join(fields))

    profiles_csv = "|".join(profile_rows)
    return (
        f"LCDProtoProfileDataset;schema={schema_version};count={len(profiles)};"
        f"settings={settings_csv};profiles={profiles_csv}"
    )


def build_profile_summaries_from_presets(
    profile_presets: Sequence[dict[str, object]],
) -> list[LCDControllerProfileSummary]:
    """@brief Convert simulator profile presets to typed protocol summaries."""

    summaries: list[LCDControllerProfileSummary] = []

    for index, preset in enumerate(profile_presets):
        normalized = normalize_lcd_profile(preset, index)
        profile_id = int(normalized.get("id", len(summaries) + 1))
        profile_name = str(normalized.get("name", f"Profile {profile_id}"))
        setpoint_value = _normalize_float(normalized.get("setpoint", 930.0), 930.0)
        target_temperature_c = setpoint_value / 10.0 if setpoint_value > 200.0 else setpoint_value
        target_pressure_bar = _normalize_float(
            preset.get("target_pressure_bar", normalized.get("tp_profiling_finish", 0.0)),
            _normalize_float(normalized.get("tp_profiling_finish", 0.0), 0.0),
        )
        target_flow_ml_s = _normalize_float(
            preset.get("target_flow_ml_sec", normalized.get("mf_profile_end", 0.0)),
            _normalize_float(normalized.get("mf_profile_end", 0.0), 0.0),
        )
        shot_target_g = _normalize_float(
            preset.get("shot_target_g", normalized.get("shot_stop_on_custom_weight", 0.0)),
            _normalize_float(normalized.get("shot_stop_on_custom_weight", 0.0), 0.0),
        )
        summaries.append(
            LCDControllerProfileSummary(
                profile_id=profile_id,
                profile_name=profile_name[:LCD_CONTROLLER_PROFILE_NAME_LENGTH],
                target_temperature_c=float(target_temperature_c),
                target_pressure_bar=float(target_pressure_bar),
                target_flow_ml_s=float(target_flow_ml_s),
                shot_target_g=float(shot_target_g),
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
