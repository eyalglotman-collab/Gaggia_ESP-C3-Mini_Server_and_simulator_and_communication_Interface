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
 * @brief Nextion pages mirrored from Gaggiuino `src/lcd/lcd.h`.
 */
typedef enum {
    LCD_PAGE_HOME = 0x00,
    LCD_PAGE_BREW_PREINFUSION = 0x01,
    LCD_PAGE_BREW_SOAK = 0x02,
    LCD_PAGE_BREW_PROFILING = 0x03,
    LCD_PAGE_BREW_MANUAL = 0x04,
    LCD_PAGE_FLUSH = 0x05,
    LCD_PAGE_DESCALE = 0x06,
    LCD_PAGE_SETTINGS_BOILER = 0x07,
    LCD_PAGE_SETTINGS_SYSTEM = 0x08,
    LCD_PAGE_BREW_GRAPH = 0x09,
    LCD_PAGE_BREW_MORE = 0x0A,
    LCD_PAGE_SHOT_SETTINGS = 0x0B,
    LCD_PAGE_BREW_TRANSITION_PROFILE = 0x0C,
    LCD_PAGE_GRAPH_PREVIEW = 0x0D,
    LCD_PAGE_KEYBOARD_NUMERIC = 0x0E,
    LCD_PAGE_LED = 0x0F,
} lcd_page_id_t;

/**
 * @brief User-selected operation mode from LCD `modeSelect`.
 *
 * Mirrors Gaggiuino `OPERATION_MODES` ordering.
 */
typedef enum {
    LCD_OPMODE_STRAIGHT_9BAR = 0,
    LCD_OPMODE_JUST_PREINFUSION = 1,
    LCD_OPMODE_JUST_PRESSURE_PROFILE = 2,
    LCD_OPMODE_MANUAL = 3,
    LCD_OPMODE_PREINFUSION_AND_PRESSURE_PROFILE = 4,
    LCD_OPMODE_FLUSH = 5,
    LCD_OPMODE_DESCALE = 6,
    LCD_OPMODE_FLOW_PREINFUSION_STRAIGHT_9BAR_PROFILING = 7,
    LCD_OPMODE_JUST_FLOW_BASED_PROFILING = 8,
    LCD_OPMODE_STEAM = 9,
    LCD_OPMODE_FLOW_BASED_PREINFUSION_PRESSURE_BASED_PROFILING = 10,
    LCD_OPMODE_EVERYTHING_FLOW_PROFILED = 11,
    LCD_OPMODE_PRESSURE_BASED_PREINFUSION_AND_FLOW_PROFILE = 12,
} lcd_operational_mode_t;

/**
 * @brief Target heating state sent controller -> LCD (`targetState`).
 *
 * Mirrors Gaggiuino `HEATING` ordering.
 */
typedef enum {
    LCD_TARGET_HEATING_BREW = 0,
    LCD_TARGET_HEATING_STEAM = 1,
    LCD_TARGET_HEATING_HOT_WATER = 2,
} lcd_target_heating_state_t;

/**
 * @brief LCD trigger IDs raised from Nextion callbacks.
 *
 * Matches Gaggiuino `triggerX` handlers in `src/lcd/nextion.cpp`.
 */
typedef enum {
    LCD_EVENT_SAVE_SETTINGS = 1,
    LCD_EVENT_SCALES_TARE = 2,
    LCD_EVENT_HOME_SCREEN_SCALES_TOGGLE = 3,
    LCD_EVENT_BREW_GRAPH_SCALES_TARE = 4,
    LCD_EVENT_REFRESH_ELEMENTS = 6,
    LCD_EVENT_QUICK_PROFILE_SWITCH = 7,
    LCD_EVENT_SAVE_PROFILE = 8,
    LCD_EVENT_RESET_SETTINGS = 9,
    LCD_EVENT_LOAD_DEFAULT_PROFILE = 10,
} lcd_controller_event_t;

/**
 * @brief Controller/system runtime state that can affect LCD behavior.
 */
typedef struct {
    bool startup_init_finished;
    bool brew_active;
    bool non_brew_mode_active;
    bool warmup_enabled;
    bool home_screen_scales_enabled;
    bool timer_running;
    lcd_page_id_t current_page;
    lcd_page_id_t last_page;
    lcd_target_heating_state_t target_heating_state;
    lcd_operational_mode_t selected_opmode;
} lcd_controller_system_state_t;

/**
 * @brief Live sensor state mirrored from Gaggiuino `SensorState`.
 */
typedef struct {
    bool brew_switch_state;
    bool steam_switch_state;
    bool hot_water_switch_state;
    bool steam_forgotten_on;
    bool scales_present;
    bool tare_pending;
    float temperature_c;
    float water_temperature_c;
    float pressure_bar;
    float pressure_change_speed_bar_s;
    float pump_flow_ml_s;
    float pump_flow_change_speed_ml_s2;
    float water_pumped_ml;
    float weight_flow_g_s;
    float weight_g;
    float shot_weight_g;
    float smoothed_pressure_bar;
    float smoothed_pump_flow_ml_s;
    float smoothed_weight_flow_g_s;
    float considered_flow_ml_s;
    int32_t pump_clicks;
    uint16_t water_level_raw;
    bool tof_ready;
} lcd_controller_sensor_state_t;

/**
 * @brief Reduced runtime snapshot for graph/telemetry updates.
 */
typedef struct {
    bool brew_active;
    bool steam_active;
    bool scales_present;
    float temperature_c;
    float pressure_bar;
    float pump_flow_ml_s;
    float weight_flow_g_s;
    float weight_g;
    uint16_t water_level_raw;
} lcd_controller_sensor_snapshot_t;

/**
 * @brief Primary command variables read from LCD (user -> controller).
 */
typedef struct {
    uint8_t selected_profile_index;
    lcd_operational_mode_t selected_opmode;
    uint16_t descale_cycle;
    uint16_t manual_flow_volume;
    bool preinfusion_flow_mode;
    bool profile_flow_mode;
    bool transition_flow_mode;
    bool home_screen_scales_enabled;
    uint8_t shot_preset;
} lcd_to_controller_command_state_t;

/**
 * @brief Primary values written by controller to LCD (controller -> user).
 */
typedef struct {
    float pressure_bar;
    uint16_t temperature_c;
    uint16_t temperature_decimal;
    float weight_g;
    uint16_t flow_value;
    float uptime_s;
    uint16_t tank_water_level;
    uint32_t brew_timer_seconds;
    bool brew_timer_running;
    lcd_target_heating_state_t target_heating_state;
} controller_to_lcd_runtime_state_t;

#ifdef __cplusplus
}
#endif
