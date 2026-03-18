/*
 * SPDX-FileCopyrightText: 2026 Eyal Espresso
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include "CommunicationFunctions.h"

/**
 * @brief ESP-IDF application entry point for the bridge firmware.
 *
 * @details Delegates runtime ownership to the communication module so the
 * transport implementation stays isolated from startup wiring.
 */
void app_main(void)
{
    communication_functions_run();
}
