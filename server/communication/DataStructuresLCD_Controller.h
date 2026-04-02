/*
 * SPDX-FileCopyrightText: 2026 Eyal Espresso
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Brew/Home schema version for the LCD-controller communication layer.
 */
#define LCD_CONTROLLER_BREW_SCHEMA_VERSION (1U)
#define LCD_CONTROLLER_DATA_SCHEMA_VERSION (12U)

/**
 * @brief Maximum profile count and profile-name length for Brew/Home sync.
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
 * @brief Legacy DATA integer-slot map currently used by the Brew UI screen.
 */
typedef enum {
    LCD_CONTROLLER_BREW_SLOT_SEQUENCE = 0,
    LCD_CONTROLLER_BREW_SLOT_PROFILE_ID = 1,
    LCD_CONTROLLER_BREW_SLOT_BREW_ELAPSED_MS = 2,
    LCD_CONTROLLER_BREW_SLOT_BREW_DURATION_MS = 3,
    LCD_CONTROLLER_BREW_SLOT_TARGET_PRESSURE_MBAR = 4,
    LCD_CONTROLLER_BREW_SLOT_TARGET_FLOW_ML_S_X1000 = 5,
    LCD_CONTROLLER_BREW_SLOT_TARGET_TEMP_MILLIC = 6,
    LCD_CONTROLLER_BREW_SLOT_WATER_LEVEL_X10 = 7,
    LCD_CONTROLLER_BREW_SLOT_WEIGHT_X100 = 8,
    LCD_CONTROLLER_BREW_SLOT_WARMUP_BOOL = 9,
    LCD_CONTROLLER_BREW_SLOT_STEAM_BOOL = 10,
    LCD_CONTROLLER_BREW_SLOT_UPTIME_MIN_X10 = 11,
    LCD_CONTROLLER_BREW_SLOT_SHOT_TARGET_G_X100 = 12,
    LCD_CONTROLLER_BREW_SLOT_LIVE_PRESSURE_MBAR = 13,
    LCD_CONTROLLER_BREW_SLOT_VALIDITY_MASK = 14,
    LCD_CONTROLLER_BREW_SLOT_COUNT = 20,
} lcd_controller_brew_slot_t;

/**
 * @brief Validity-mask bits for simulator-authored Brew/Home fields.
 */
typedef enum {
    LCD_CONTROLLER_BREW_VALID_SHOT_TIMER = (1U << 0),
    LCD_CONTROLLER_BREW_VALID_LIVE_PRESSURE = (1U << 1),
    LCD_CONTROLLER_BREW_VALID_WATER_LEVEL = (1U << 2),
    LCD_CONTROLLER_BREW_VALID_WEIGHT = (1U << 3),
    LCD_CONTROLLER_BREW_VALID_WARMUP = (1U << 4),
} lcd_controller_brew_validity_mask_t;

/**
 * @brief Lightweight profile summary exchanged for Brew screen controls.
 */
typedef struct {
    uint8_t profile_id;
    char profile_name[LCD_CONTROLLER_PROFILE_NAME_LENGTH];
    float target_temperature_c;
    float target_pressure_bar;
    float target_flow_ml_s;
    float shot_target_g;
} lcd_controller_profile_summary_t;

/**
 * @brief Brew-profile catalog cached by the LCD communication layer.
 */
typedef struct {
    uint8_t count;
    lcd_controller_profile_summary_t entries[LCD_CONTROLLER_MAX_PROFILES];
} lcd_controller_profile_catalog_t;

/**
 * @brief Per-profile EEPROM-mirrored profile struct.
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
 * @brief Full profile/settings dataset exchanged during protocol bootstrap.
 */
typedef struct {
    uint16_t schema_version;
    lcd_controller_settings_t settings;
    lcd_controller_profile_t profiles[LCD_CONTROLLER_MAX_PROFILES];
} lcd_controller_dataset_t;

/**
 * @brief Runtime Brew/Home values displayed on the Brew tab.
 */
typedef struct {
    uint8_t profile_id;
    uint32_t brew_elapsed_ms;
    uint32_t brew_duration_ms;
    float target_temperature_c;
    float target_pressure_bar;
    float target_flow_ml_s;
    float live_pressure_bar;
    float live_temperature_c;
    float live_water_level_pct;
    float live_weight_g;
    float shot_target_preview_g;
    bool warmup_on;
    bool steam_on;
    float uptime_minutes;
} lcd_controller_brew_home_state_t;

#ifdef __cplusplus
}
#endif
