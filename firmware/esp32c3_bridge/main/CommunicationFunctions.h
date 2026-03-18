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

#define BRIDGE_REALTIME_TEMP_COUNT (20U)
#define BRIDGE_REALTIME_DATA_INTERFACE_VERSION (1U)
#define BRIDGE_REALTIME_DATA_PAYLOAD_BYTES (10U + (BRIDGE_REALTIME_TEMP_COUNT * sizeof(double)))

/**
 * @brief Run the ESP32-C3 communication bridge runtime loop.
 *
 * @details Initializes USB transport, Wi-Fi SoftAP, and TCP listener services,
 * then executes the continuous framed-communication polling loop.
 */
void communication_functions_run(void);

#ifdef __cplusplus
}
#endif
