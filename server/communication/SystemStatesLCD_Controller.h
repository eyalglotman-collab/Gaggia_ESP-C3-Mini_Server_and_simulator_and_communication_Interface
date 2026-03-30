/*
 * SPDX-FileCopyrightText: 2026 Eyal Espresso
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Client-side LCD protocol link state for Brew/Home data bootstrap.
 */
typedef enum {
    LCD_CONTROLLER_PROTOCOL_STATE_UNINITIALIZED = 0,
    LCD_CONTROLLER_PROTOCOL_STATE_READY,
    LCD_CONTROLLER_PROTOCOL_STATE_WAIT_SCHEMA_ACK,
    LCD_CONTROLLER_PROTOCOL_STATE_SCHEMA_ACKED,
} lcd_controller_protocol_link_state_t;

/**
 * @brief Peer text-event decoding outcomes for protocol bootstrap handling.
 */
typedef enum {
    LCD_CONTROLLER_PROTOCOL_EVENT_NONE = 0,
    LCD_CONTROLLER_PROTOCOL_EVENT_SCHEMA_ACK,
    LCD_CONTROLLER_PROTOCOL_EVENT_PROFILE_CATALOG,
} lcd_controller_protocol_event_t;

/**
 * @brief Protocol bootstrap command strings sent from client to simulator.
 */
#define LCD_CONTROLLER_PROTOCOL_CMD_INIT \
    "LCDProtoInit;schema=brew_home_v1;version=1"

#define LCD_CONTROLLER_PROTOCOL_CMD_PROFILE_CATALOG_GET \
    "LCDProtoProfileCatalogGet"

/**
 * @brief Protocol peer event prefixes received from simulator.
 */
#define LCD_CONTROLLER_PROTOCOL_EVENT_PREFIX_ACK \
    "LCDProtoAck"

#define LCD_CONTROLLER_PROTOCOL_EVENT_PREFIX_PROFILE_CATALOG \
    "LCDProtoProfileCatalog"

#ifdef __cplusplus
}
#endif
