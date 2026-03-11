/*
 * SPDX-FileCopyrightText: 2026 Eyal Espresso
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/*
 * @brief ESP32-C3 bridge firmware baseline for the simulator transport path.
 *
 * @details The canonical firmware version for this bridge project is tracked in
 * `firmware/esp32c3_bridge/VERSION` using the same X.Y.Z rules as the
 * simulator application version tree. Keep firmware-facing documentation and
 * any exposed runtime metadata aligned with that file when the bridge behavior
 * changes.
 */

#include <stdint.h>
#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define BRIDGE_RX_BUFFER_SIZE           512
#define BRIDGE_TX_BUFFER_SIZE           512
#define BRIDGE_FRAME_MAX_PAYLOAD        128
#define BRIDGE_FRAME_OVERHEAD           17
#define BRIDGE_FRAME_MAX_SIZE           (BRIDGE_FRAME_MAX_PAYLOAD + BRIDGE_FRAME_OVERHEAD)
#define BRIDGE_POLL_DELAY_MS            20
#define BRIDGE_DEVICE_LIVE_START        1U

#define BRIDGE_SOF_BYTE0                0xA5
#define BRIDGE_SOF_BYTE1                0x5A

typedef enum {
    BRIDGE_STATE_RESET = 0,
    BRIDGE_STATE_INITIALIZE = 1,
    BRIDGE_STATE_CONNECT = 2,
    BRIDGE_STATE_DISCONNECT = 3,
    BRIDGE_STATE_ERROR = 4,
} bridge_state_t;

typedef enum {
    BRIDGE_MESSAGE_RESET = 1,
    BRIDGE_MESSAGE_INITIALIZE = 2,
    BRIDGE_MESSAGE_CONNECT = 3,
    BRIDGE_MESSAGE_DISCONNECT = 4,
    BRIDGE_MESSAGE_KEEPALIVE = 5,
    BRIDGE_MESSAGE_ERROR = 6,
    BRIDGE_MESSAGE_ACK = 7,
    BRIDGE_MESSAGE_DATA = 8,
} bridge_message_type_t;

typedef struct {
    bridge_message_type_t message_type;
    uint32_t host_live_integer;
    uint32_t device_live_integer;
    uint16_t sequence;
    uint16_t payload_length;
    uint8_t payload[BRIDGE_FRAME_MAX_PAYLOAD];
} bridge_frame_t;

static bridge_state_t s_bridge_state = BRIDGE_STATE_RESET;
static uint32_t s_device_live_integer = BRIDGE_DEVICE_LIVE_START;

/**
 * @brief Compute CRC16-CCITT for the provided byte sequence.
 *
 * @details Uses the same low-level integrity algorithm as the simulator host
 * framing code so the bridge can validate and generate frames compatibly.
 *
 * @param[in] data Source byte buffer.
 * @param[in] length Number of source bytes.
 *
 * @return CRC16-CCITT value.
 */
static uint16_t bridge_crc16_ccitt(const uint8_t *data, size_t length)
{
    uint16_t crc = 0xFFFF;

    for (size_t i = 0; i < length; ++i) {
        crc ^= (uint16_t)data[i] << 8;
        for (int bit = 0; bit < 8; ++bit) {
            if ((crc & 0x8000U) != 0U) {
                crc = (uint16_t)((crc << 1) ^ 0x1021U);
            } else {
                crc <<= 1;
            }
        }
    }

    return crc;
}

/**
 * @brief Parse one complete framed packet from the RX buffer.
 *
 * @details The function expects the simulator framing layout:
 * SOF, type, payload length, host/device counters, sequence, payload, CRC.
 *
 * @param[in] data Raw frame bytes.
 * @param[in] length Raw frame length.
 * @param[out] out_frame Parsed frame on success.
 *
 * @return
 *      - ESP_OK if the frame is valid
 *      - ESP_ERR_INVALID_SIZE if the frame is incomplete or too large
 *      - ESP_ERR_INVALID_CRC if the frame CRC is invalid
 *      - ESP_ERR_INVALID_ARG if required framing fields are invalid
 */
static esp_err_t bridge_parse_frame(const uint8_t *data, size_t length, bridge_frame_t *out_frame)
{
    uint16_t payload_length = 0;
    uint16_t expected_crc = 0;
    uint16_t actual_crc = 0;

    if (length < BRIDGE_FRAME_OVERHEAD || out_frame == NULL) {
        return ESP_ERR_INVALID_SIZE;
    }

    if (data[0] != BRIDGE_SOF_BYTE0 || data[1] != BRIDGE_SOF_BYTE1) {
        return ESP_ERR_INVALID_ARG;
    }

    payload_length = (uint16_t)(data[3] | ((uint16_t)data[4] << 8));
    if (payload_length > BRIDGE_FRAME_MAX_PAYLOAD) {
        return ESP_ERR_INVALID_SIZE;
    }

    if (length != (size_t)(BRIDGE_FRAME_OVERHEAD + payload_length)) {
        return ESP_ERR_INVALID_SIZE;
    }

    expected_crc = (uint16_t)(data[length - 2] | ((uint16_t)data[length - 1] << 8));
    actual_crc = bridge_crc16_ccitt(data, length - 2);
    if (expected_crc != actual_crc) {
        return ESP_ERR_INVALID_CRC;
    }

    out_frame->message_type = (bridge_message_type_t)data[2];
    out_frame->payload_length = payload_length;
    out_frame->host_live_integer = (uint32_t)data[5]
        | ((uint32_t)data[6] << 8)
        | ((uint32_t)data[7] << 16)
        | ((uint32_t)data[8] << 24);
    out_frame->device_live_integer = (uint32_t)data[9]
        | ((uint32_t)data[10] << 8)
        | ((uint32_t)data[11] << 16)
        | ((uint32_t)data[12] << 24);
    out_frame->sequence = (uint16_t)(data[13] | ((uint16_t)data[14] << 8));
    if (payload_length > 0U) {
        memcpy(out_frame->payload, &data[15], payload_length);
    }

    return ESP_OK;
}

/**
 * @brief Encode and transmit one framed response to the simulator host.
 *
 * @details Replies always use the same frame envelope as the PC host.
 *
 * @param[in] message_type Response packet type.
 * @param[in] request_frame Source request used for host counter and sequence.
 * @param[in] payload_text Optional UTF-8 payload text.
 */
static void bridge_send_frame(
    bridge_message_type_t message_type,
    const bridge_frame_t *request_frame,
    const char *payload_text)
{
    uint8_t frame[BRIDGE_FRAME_MAX_SIZE] = {0};
    size_t payload_length = 0U;
    size_t frame_length = 0U;
    uint16_t crc = 0U;

    if (request_frame == NULL) {
        return;
    }

    if (payload_text != NULL) {
        payload_length = strlen(payload_text);
        if (payload_length > BRIDGE_FRAME_MAX_PAYLOAD) {
            payload_length = BRIDGE_FRAME_MAX_PAYLOAD;
        }
    }

    frame[0] = BRIDGE_SOF_BYTE0;
    frame[1] = BRIDGE_SOF_BYTE1;
    frame[2] = (uint8_t)message_type;
    frame[3] = (uint8_t)(payload_length & 0xFFU);
    frame[4] = (uint8_t)((payload_length >> 8) & 0xFFU);
    frame[5] = (uint8_t)(request_frame->host_live_integer & 0xFFU);
    frame[6] = (uint8_t)((request_frame->host_live_integer >> 8) & 0xFFU);
    frame[7] = (uint8_t)((request_frame->host_live_integer >> 16) & 0xFFU);
    frame[8] = (uint8_t)((request_frame->host_live_integer >> 24) & 0xFFU);
    frame[9] = (uint8_t)(s_device_live_integer & 0xFFU);
    frame[10] = (uint8_t)((s_device_live_integer >> 8) & 0xFFU);
    frame[11] = (uint8_t)((s_device_live_integer >> 16) & 0xFFU);
    frame[12] = (uint8_t)((s_device_live_integer >> 24) & 0xFFU);
    frame[13] = (uint8_t)(request_frame->sequence & 0xFFU);
    frame[14] = (uint8_t)((request_frame->sequence >> 8) & 0xFFU);

    if (payload_length > 0U) {
        memcpy(&frame[15], payload_text, payload_length);
    }

    frame_length = BRIDGE_FRAME_OVERHEAD + payload_length;
    crc = bridge_crc16_ccitt(frame, frame_length - 2U);
    frame[frame_length - 2U] = (uint8_t)(crc & 0xFFU);
    frame[frame_length - 1U] = (uint8_t)((crc >> 8) & 0xFFU);

    (void)usb_serial_jtag_write_bytes(frame, frame_length, pdMS_TO_TICKS(BRIDGE_POLL_DELAY_MS));
    s_device_live_integer += 1U;
}

/**
 * @brief Install the USB-Serial/JTAG driver used by the host COM port.
 *
 * @details The PC simulator talks to the ESP32-C3 over the USB CDC/JTAG
 * virtual COM interface, not UART0. The bridge must therefore use the
 * dedicated USB-Serial/JTAG driver and keep the channel free of application
 * log text so only framed packets traverse COM4.
 *
 * @return
 *      - ESP_OK on success
 *      - ESP_FAIL on installation/configuration failure
 */
static esp_err_t bridge_transport_init(void)
{
    usb_serial_jtag_driver_config_t driver_config = {
        .tx_buffer_size = BRIDGE_TX_BUFFER_SIZE,
        .rx_buffer_size = BRIDGE_RX_BUFFER_SIZE,
    };

    esp_log_level_set("*", ESP_LOG_NONE);

    return usb_serial_jtag_driver_install(&driver_config);
}

/**
 * @brief Update the bridge state from one validated request frame.
 *
 * @details The minimal firmware baseline acknowledges reset/initialize/connect/
 * disconnect/keepalive/data traffic so the simulator host can exercise its
 * state machine without suffering serial write timeouts.
 *
 * @param[in] frame Parsed request frame.
 */
static void bridge_handle_frame(const bridge_frame_t *frame)
{
    if (frame == NULL) {
        return;
    }

    switch (frame->message_type) {
    case BRIDGE_MESSAGE_RESET:
        s_bridge_state = BRIDGE_STATE_RESET;
        bridge_send_frame(BRIDGE_MESSAGE_ACK, frame, "reset_ack");
        break;
    case BRIDGE_MESSAGE_INITIALIZE:
        s_bridge_state = BRIDGE_STATE_INITIALIZE;
        bridge_send_frame(BRIDGE_MESSAGE_ACK, frame, "initialize_ack");
        break;
    case BRIDGE_MESSAGE_CONNECT:
        s_bridge_state = BRIDGE_STATE_CONNECT;
        bridge_send_frame(BRIDGE_MESSAGE_ACK, frame, "connect_ack");
        break;
    case BRIDGE_MESSAGE_DISCONNECT:
        s_bridge_state = BRIDGE_STATE_DISCONNECT;
        bridge_send_frame(BRIDGE_MESSAGE_ACK, frame, "disconnect_ack");
        break;
    case BRIDGE_MESSAGE_KEEPALIVE:
        bridge_send_frame(BRIDGE_MESSAGE_KEEPALIVE, frame, "keepalive_ack");
        break;
    case BRIDGE_MESSAGE_DATA:
        bridge_send_frame(BRIDGE_MESSAGE_ACK, frame, "data_ack");
        break;
    case BRIDGE_MESSAGE_ERROR:
        s_bridge_state = BRIDGE_STATE_ERROR;
        bridge_send_frame(BRIDGE_MESSAGE_ACK, frame, "error_ack");
        break;
    case BRIDGE_MESSAGE_ACK:
    default:
        bridge_send_frame(BRIDGE_MESSAGE_ERROR, frame, "unsupported");
        break;
    }
}

/**
 * @brief Poll the bridge serial port and process complete frames.
 *
 * @details The baseline firmware drains incoming host frames and responds with
 * framed acknowledgements so the PC simulator can verify the serial protocol
 * path before Wi-Fi/TCP bridge logic is added.
 */
void app_main(void)
{
    uint8_t rx_buffer[BRIDGE_FRAME_MAX_SIZE] = {0};
    esp_err_t init_result = bridge_transport_init();

    if (init_result != ESP_OK) {
        s_bridge_state = BRIDGE_STATE_ERROR;
        while (1) {
            vTaskDelay(pdMS_TO_TICKS(250));
        }
    }

    while (1) {
        int received = usb_serial_jtag_read_bytes(
            rx_buffer,
            sizeof(rx_buffer),
            pdMS_TO_TICKS(BRIDGE_POLL_DELAY_MS));

        if (received <= 0) {
            vTaskDelay(pdMS_TO_TICKS(BRIDGE_POLL_DELAY_MS));
            continue;
        }

        if (received < BRIDGE_FRAME_OVERHEAD) {
            continue;
        }

        bridge_frame_t frame = {0};
        esp_err_t parse_result = bridge_parse_frame(rx_buffer, (size_t)received, &frame);
        if (parse_result != ESP_OK) {
            continue;
        }

        bridge_handle_frame(&frame);
    }
}
