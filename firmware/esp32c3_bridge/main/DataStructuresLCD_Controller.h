/*
 * SPDX-FileCopyrightText: 2026 Eyal Espresso
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Data schema version mirrored from Gaggiuino EEPROM metadata.
 */
#define LCD_CONTROLLER_DATA_SCHEMA_VERSION (12U)

/**
 * @brief Max profile count and max profile name length mirrored from Gaggiuino.
 */
#define LCD_CONTROLLER_MAX_PROFILES (5U)
#define LCD_CONTROLLER_PROFILE_NAME_LENGTH (25U)

/**
 * @brief Transition curve ordering mirrored from Gaggiuino `TransitionCurve`.
 */
typedef enum {
    LCD_TRANSITION_CURVE_EASE_IN_OUT = 0,
    LCD_TRANSITION_CURVE_EASE_IN = 1,
    LCD_TRANSITION_CURVE_EASE_OUT = 2,
    LCD_TRANSITION_CURVE_LINEAR = 3,
    LCD_TRANSITION_CURVE_INSTANT = 4,
} lcd_transition_curve_t;

/**
 * @brief Profiling phase type mirrored from Gaggiuino `PHASE_TYPE`.
 */
typedef enum {
    LCD_PHASE_TYPE_FLOW = 0,
    LCD_PHASE_TYPE_PRESSURE = 1,
} lcd_phase_type_t;

/**
 * @brief Per-profile data mirrored from `eepromValues_t::profile_t`.
 */
typedef struct {
    char name[LCD_CONTROLLER_PROFILE_NAME_LENGTH];

    bool preinfusion_state;
    bool preinfusion_flow_state;
    uint16_t preinfusion_sec;
    float preinfusion_bar;
    float preinfusion_flow_vol;
    uint16_t preinfusion_flow_time;
    float preinfusion_flow_pressure_target;
    float preinfusion_pressure_flow_target;
    float preinfusion_filled;
    bool preinfusion_pressure_above;
    float preinfusion_weight_above;

    bool soak_state;
    uint16_t soak_time_pressure;
    uint16_t soak_time_flow;
    float soak_keep_pressure;
    float soak_keep_flow;
    float soak_below_pressure;
    float soak_above_pressure;
    float soak_above_weight;

    uint16_t preinfusion_ramp;
    uint16_t preinfusion_ramp_slope;

    bool tp_state;
    bool tp_type;
    float tp_profiling_start;
    float tp_profiling_finish;
    uint16_t tp_profiling_hold;
    float tp_profiling_hold_limit;
    uint16_t tp_profiling_slope;
    uint16_t tp_profiling_slope_shape;
    float tp_profiling_flow_restriction;

    float tf_profile_start;
    float tf_profile_end;
    uint16_t tf_profile_hold;
    float tf_profile_hold_limit;
    uint16_t tf_profile_slope;
    uint16_t tf_profile_slope_shape;
    float tf_profiling_pressure_restriction;

    bool profiling_state;
    bool mf_profile_state;
    float mp_profiling_start;
    float mp_profiling_finish;
    uint16_t mp_profiling_slope;
    uint16_t mp_profiling_slope_shape;
    float mp_profiling_flow_restriction;

    float mf_profile_start;
    float mf_profile_end;
    uint16_t mf_profile_slope;
    uint16_t mf_profile_slope_shape;
    float mf_profiling_pressure_restriction;

    uint16_t setpoint;
    bool stop_on_weight_state;
    float shot_dose;
    float shot_stop_on_custom_weight;
    uint16_t shot_preset;
} lcd_controller_profile_t;

/**
 * @brief Global settings/calibration values mirrored from `eepromValues_t`.
 */
typedef struct {
    uint16_t steam_setpoint;
    uint16_t offset_temp;
    uint16_t hpwr;
    uint16_t main_divider;
    uint16_t brew_divider;

    uint8_t active_profile;

    uint16_t power_line_frequency;
    uint16_t lcd_sleep;
    bool warmup_state;
    bool home_on_shot_finish;
    bool brew_delta_state;
    bool basket_prefill;

    int32_t scales_f1;
    int32_t scales_f2;
    float pump_flow_at_zero;

    bool led_state;
    bool led_disco;
    uint8_t led_r;
    uint8_t led_g;
    uint8_t led_b;
} lcd_controller_settings_t;

/**
 * @brief Full payload contract for LCD/controller profile + settings sync.
 */
typedef struct {
    uint16_t schema_version;
    lcd_controller_settings_t settings;
    lcd_controller_profile_t profiles[LCD_CONTROLLER_MAX_PROFILES];
} lcd_controller_dataset_t;

/**
 * @brief Runtime shot data shown on LCD graph pages.
 *
 * Mirrors Gaggiuino `ShotSnapshot`.
 */
typedef struct {
    uint32_t time_in_shot_ms;
    float pressure_bar;
    float pump_flow_ml_s;
    float weight_flow_g_s;
    float temperature_c;
    float shot_weight_g;
    float water_pumped_ml;

    float target_temperature_c;
    float target_pump_flow_ml_s;
    float target_pressure_bar;
} lcd_controller_shot_snapshot_t;

/**
 * @brief Generic phase stop conditions used by profiling data.
 */
typedef struct {
    int32_t time_ms;
    float pressure_above;
    float pressure_below;
    float flow_above;
    float flow_below;
    float weight_above;
    float water_pumped_above;
} lcd_controller_phase_stop_conditions_t;

/**
 * @brief Generic transition segment used in profile phases.
 */
typedef struct {
    float start;
    float end;
    lcd_transition_curve_t curve;
    int32_t time_ms;
} lcd_controller_transition_t;

/**
 * @brief Generic phase representation for abstract transport.
 */
typedef struct {
    lcd_phase_type_t type;
    lcd_controller_transition_t target;
    float restriction;
    lcd_controller_phase_stop_conditions_t stop;
} lcd_controller_phase_t;

/**
 * @brief Lightweight dynamic profile view for packetization.
 */
typedef struct {
    lcd_controller_phase_t *phases;
    size_t phase_count;
    int32_t global_stop_time_ms;
    float global_stop_weight_g;
    float global_stop_water_pumped_ml;
} lcd_controller_runtime_profile_t;

#ifdef __cplusplus
}
#endif
