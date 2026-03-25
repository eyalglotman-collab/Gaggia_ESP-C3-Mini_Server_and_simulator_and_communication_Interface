/*
 * SPDX-FileCopyrightText: 2026 Eyal Espresso
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include "CommunicationFunctions.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define BRIDGE_RUNTIME_TASK_NAME "bridge_runtime"
#define BRIDGE_RUNTIME_TASK_STACK_WORDS (24576U)
#define BRIDGE_RUNTIME_TASK_PRIORITY (5U)

static const char *TAG = "bridge_main";

/**
 * @brief Dedicated bridge runtime task entry.
 *
 * @details The bridge communication module now handles multi-kilobyte frame
 * buffers (100-float simulator DATA payload support). Running it on a dedicated task
 * avoids overflowing the small default `app_main` stack.
 *
 * @param[in] arg Unused.
 */
static void bridge_runtime_task(void *arg)
{
    (void)arg;
    communication_functions_run();
    vTaskDelete(NULL);
}

/**
 * @brief ESP-IDF application entry point for the bridge firmware.
 *
 * @details Delegates runtime ownership to the communication module so the
 * transport implementation stays isolated from startup wiring.
 */
void app_main(void)
{
    BaseType_t task_result = xTaskCreate(bridge_runtime_task,
                                         BRIDGE_RUNTIME_TASK_NAME,
                                         BRIDGE_RUNTIME_TASK_STACK_WORDS,
                                         NULL,
                                         BRIDGE_RUNTIME_TASK_PRIORITY,
                                         NULL);
    if (task_result != pdPASS) {
        ESP_LOGE(TAG, "Failed to start bridge runtime task");
        while (true) {
            vTaskDelay(pdMS_TO_TICKS(250));
        }
    }
}
