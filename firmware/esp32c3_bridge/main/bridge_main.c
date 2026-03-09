#include <stdio.h>

#include "esp_chip_info.h"
#include "esp_flash.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "c3_bridge_template";

/**
 * @brief Print one-time boot and hardware summary information.
 * @details This provides a minimal bring-up baseline so the simulator project
 * can verify that the ESP32-C3 firmware toolchain, drivers, flash path, and
 * serial logging are all healthy before bridge logic is added.
 */
static void log_boot_summary(void)
{
    esp_chip_info_t chip_info;
    uint32_t flash_size_mb = 0;

    esp_chip_info(&chip_info);
    esp_flash_get_size(NULL, &flash_size_mb);

    ESP_LOGI(TAG, "ESP32-C3 bridge template boot");
    ESP_LOGI(TAG, "cores=%d revision=%d flash=%luMB features=0x%08lx",
        chip_info.cores,
        chip_info.revision,
        flash_size_mb / (1024 * 1024),
        (unsigned long)chip_info.features);
}

/**
 * @brief Minimal firmware entry point for ESP-IDF fundamentals verification.
 * @details Logs a boot summary and emits a periodic heartbeat once per second
 * so COM4 flash and monitor verification can confirm stable execution.
 */
void app_main(void)
{
    uint32_t heartbeat = 0;

    log_boot_summary();

    while (1) {
        ESP_LOGI(TAG, "heartbeat=%lu", (unsigned long)heartbeat++);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}