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

/**
 * @brief Maximum profile count and profile-name length for Brew/Home sync.
 */
#define LCD_CONTROLLER_MAX_PROFILES (8U)
#define LCD_CONTROLLER_PROFILE_NAME_LENGTH (32U)

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
