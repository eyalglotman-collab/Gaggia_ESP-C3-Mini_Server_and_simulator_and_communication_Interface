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
#include <stdbool.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"

#define BRIDGE_RX_BUFFER_SIZE           512
#define BRIDGE_TX_BUFFER_SIZE           512
#define BRIDGE_FRAME_MAX_PAYLOAD        256
#define BRIDGE_FRAME_OVERHEAD           17
#define BRIDGE_FRAME_MAX_SIZE           (BRIDGE_FRAME_MAX_PAYLOAD + BRIDGE_FRAME_OVERHEAD)
#define BRIDGE_POLL_DELAY_MS            20
#define BRIDGE_DEVICE_LIVE_START        1U
#define BRIDGE_WIFI_SSID                "EyalSimulatorAP"
#define BRIDGE_WIFI_PASSWORD            "espresso1234"
#define BRIDGE_WIFI_CHANNEL             1
#define BRIDGE_WIFI_MAX_CONNECTIONS     4
#define BRIDGE_TCP_PORT                 3333
#define BRIDGE_TCP_RX_BUFFER_SIZE       512
#define BRIDGE_KEEPALIVE_PERIOD_MS      300
#define BRIDGE_KEEPALIVE_WAIT_WINDOW_MS 300
#define BRIDGE_RUNNING_INTEGER_RETRY_LIMIT 3U

#define BRIDGE_SOF_BYTE0                0xA5
#define BRIDGE_SOF_BYTE1                0x5A

static const char *TAG = "bridge";

typedef enum {
    BRIDGE_STATE_RESET = 0,
    BRIDGE_STATE_INITIALIZE = 1,
    BRIDGE_STATE_CONNECT = 2,
    BRIDGE_STATE_DEBUG_INTERIM = 3,
    BRIDGE_STATE_KEEPALIVE = 4,
    BRIDGE_STATE_DISCONNECT = 5,
    BRIDGE_STATE_ERROR = 6,
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

typedef enum {
    BRIDGE_TRANSPORT_USB = 0,
    BRIDGE_TRANSPORT_TCP = 1,
} bridge_transport_t;

static bridge_state_t s_bridge_state = BRIDGE_STATE_RESET;
static uint32_t s_server_live_integer = 0U;
static uint32_t s_client_live_integer = 0U;
static uint16_t s_usb_sequence = 0U;
static uint16_t s_tcp_sequence = 0U;
static bool s_wifi_stack_initialized = false;
static bool s_wifi_transport_enabled = true;
static int s_tcp_listen_fd = -1;
static int s_tcp_client_fd = -1;
static int64_t s_last_tcp_activity_us = 0;
static int64_t s_last_keepalive_tx_us = 0;
static size_t s_tcp_rx_length = 0U;
static uint8_t s_tcp_rx_buffer[BRIDGE_TCP_RX_BUFFER_SIZE] = {0};
static bool s_keepalive_response_pending = false;
static bool s_keepalive_window_has_message = false;
static uint32_t s_running_integer_retry_count = 0U;
static uint32_t s_keepalive_empty_window_count = 0U;
static uint32_t s_timeout_event_count = 0U;
static uint32_t s_active_session_id = 0U;
static uint32_t s_next_session_id = 1U;
static uint32_t s_next_keepalive_request_id = 1U;
static uint32_t s_pending_keepalive_request_id = 0U;
static uint32_t s_last_completed_keepalive_request_id = 0U;
static bool s_interim_debug_logged = false;
static int64_t s_keepalive_window_started_us = 0;
static int64_t s_reset_cycle_started_us = 0;

static const char *bridge_state_to_string(bridge_state_t state);
static void bridge_enter_state(bridge_state_t next_state);
static esp_err_t bridge_tcp_server_init(void);
static void bridge_close_tcp_client(void);
static void bridge_notify_usb_fault(const char *payload_text);
static bool bridge_try_parse_u32_payload_value(const char *payload_text,
                                               const char *key_text,
                                               uint32_t *out_value);
static void bridge_build_keepalive_payload(char *buffer,
                                           size_t buffer_length,
                                           uint32_t session_id,
                                           uint32_t request_id);
static void bridge_send_server_keepalive(bool reuse_pending_request_id);
static void bridge_start_keepalive_window(bool clear_transport_buffer);
static void bridge_mark_keepalive_window_message(void);
static void bridge_log_interim_debug_state(void);
static void bridge_service_debug_interim_state(void);
static void bridge_service_keepalive_engine(void);
static void bridge_service_transport_watchdog(void);
static void bridge_handle_running_integer_failure(const char *fault_reason);

/**
 * @brief Convert one bridge state enum into printable text.
 *
 * @details Keeps state transition logs readable during protocol debugging.
 *
 * @param[in] state Bridge runtime state.
 *
 * @return Constant state name.
 */
static const char *bridge_state_to_string(bridge_state_t state)
{
    switch (state) {
    case BRIDGE_STATE_RESET:
        return "Reset";
    case BRIDGE_STATE_INITIALIZE:
        return "Initialize";
    case BRIDGE_STATE_CONNECT:
        return "Connect";
    case BRIDGE_STATE_DEBUG_INTERIM:
        return "InterimDebug";
    case BRIDGE_STATE_KEEPALIVE:
        return "KeepAlive";
    case BRIDGE_STATE_DISCONNECT:
        return "Disconnect";
    case BRIDGE_STATE_ERROR:
        return "Error";
    default:
        return "Unknown";
    }
}

/**
 * @brief Transition the bridge runtime state with uniform bookkeeping.
 *
 * @details Records reset-cycle start timestamps and clears one-shot interim
 * logging flags whenever the state machine leaves interim-debug.
 *
 * @param[in] next_state New state to enter.
 */
static void bridge_enter_state(bridge_state_t next_state)
{
    if (s_bridge_state != next_state) {
        ESP_LOGI(TAG,
                 "State %s -> %s",
                 bridge_state_to_string(s_bridge_state),
                 bridge_state_to_string(next_state));
    }

    s_bridge_state = next_state;
    if (next_state == BRIDGE_STATE_RESET) {
        s_reset_cycle_started_us = esp_timer_get_time();
        s_running_integer_retry_count = 0U;
        s_timeout_event_count = 0U;
        s_keepalive_empty_window_count = 0U;
        s_keepalive_window_started_us = 0;
        s_keepalive_window_has_message = false;
        s_active_session_id = 0U;
        s_pending_keepalive_request_id = 0U;
        s_last_completed_keepalive_request_id = 0U;
        s_next_keepalive_request_id = 1U;
    }
    if (next_state != BRIDGE_STATE_DEBUG_INTERIM) {
        s_interim_debug_logged = false;
    }
}

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
 * @brief Parse one unsigned payload field formatted as `key=value`.
 *
 * @details The parser accepts semicolon-delimited metadata tokens and returns
 * `false` when the key is absent or malformed.
 *
 * @param[in] payload_text NUL-terminated payload text.
 * @param[in] key_text Field key without the equals sign.
 * @param[out] out_value Parsed unsigned value.
 *
 * @return `true` when the field parses successfully; otherwise `false`.
 */
static bool bridge_try_parse_u32_payload_value(const char *payload_text,
                                               const char *key_text,
                                               uint32_t *out_value)
{
    size_t key_len = 0U;
    const char *token = NULL;
    const char *value_start = NULL;
    char *end_ptr = NULL;
    unsigned long parsed_value = 0UL;

    if (payload_text == NULL || key_text == NULL || out_value == NULL) {
        return false;
    }

    key_len = strlen(key_text);
    if (key_len == 0U) {
        return false;
    }

    token = payload_text;
    while ((token = strstr(token, key_text)) != NULL) {
        bool token_start_ok = (token == payload_text) || (*(token - 1) == ';');
        if (!token_start_ok || token[key_len] != '=') {
            token += key_len;
            continue;
        }

        value_start = token + key_len + 1U;
        parsed_value = strtoul(value_start, &end_ptr, 10);
        if (end_ptr == value_start || parsed_value > UINT32_MAX) {
            return false;
        }
        if (*end_ptr != '\0' && *end_ptr != ';') {
            return false;
        }

        *out_value = (uint32_t)parsed_value;
        return true;
    }

    return false;
}

/**
 * @brief Build one keepalive request payload with correlation metadata.
 *
 * @details The bridge owns session and request identifiers and sends both so
 * the client can echo them in keepalive responses.
 *
 * @param[out] buffer Destination payload buffer.
 * @param[in] buffer_length Destination buffer size in bytes.
 * @param[in] session_id Active bridge session id.
 * @param[in] request_id Keepalive request id for this exchange.
 */
static void bridge_build_keepalive_payload(char *buffer,
                                           size_t buffer_length,
                                           uint32_t session_id,
                                           uint32_t request_id)
{
    if (buffer == NULL || buffer_length == 0U) {
        return;
    }

    snprintf(buffer,
             buffer_length,
             "ka_req;sid=%" PRIu32 ";req=%" PRIu32,
             session_id,
             request_id);
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
    bridge_transport_t transport,
    bridge_message_type_t message_type,
    const bridge_frame_t *request_frame,
    const char *payload_text)
{
    uint8_t frame[BRIDGE_FRAME_MAX_SIZE] = {0};
    size_t payload_length = 0U;
    size_t frame_length = 0U;
    uint16_t crc = 0U;
    uint16_t tx_sequence = 0U;

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
    frame[5] = (uint8_t)(s_server_live_integer & 0xFFU);
    frame[6] = (uint8_t)((s_server_live_integer >> 8) & 0xFFU);
    frame[7] = (uint8_t)((s_server_live_integer >> 16) & 0xFFU);
    frame[8] = (uint8_t)((s_server_live_integer >> 24) & 0xFFU);
    frame[9] = (uint8_t)(s_client_live_integer & 0xFFU);
    frame[10] = (uint8_t)((s_client_live_integer >> 8) & 0xFFU);
    frame[11] = (uint8_t)((s_client_live_integer >> 16) & 0xFFU);
    frame[12] = (uint8_t)((s_client_live_integer >> 24) & 0xFFU);
    tx_sequence = request_frame->sequence;
    if (transport == BRIDGE_TRANSPORT_TCP) {
        tx_sequence = s_tcp_sequence++;
    }
    frame[13] = (uint8_t)(tx_sequence & 0xFFU);
    frame[14] = (uint8_t)((tx_sequence >> 8) & 0xFFU);

    if (payload_length > 0U) {
        memcpy(&frame[15], payload_text, payload_length);
    }

    frame_length = BRIDGE_FRAME_OVERHEAD + payload_length;
    crc = bridge_crc16_ccitt(frame, frame_length - 2U);
    frame[frame_length - 2U] = (uint8_t)(crc & 0xFFU);
    frame[frame_length - 1U] = (uint8_t)((crc >> 8) & 0xFFU);

    if (transport == BRIDGE_TRANSPORT_USB) {
        (void)usb_serial_jtag_write_bytes(frame, frame_length, pdMS_TO_TICKS(BRIDGE_POLL_DELAY_MS));
    } else if (transport == BRIDGE_TRANSPORT_TCP && s_tcp_client_fd >= 0) {
        (void)send(s_tcp_client_fd, frame, frame_length, 0);
    }
}

/**
 * @brief Send one unsolicited bridge status frame to the simulator host.
 *
 * @details The ESP32-C3 owns the real Wi-Fi/TCP session with the client. When
 * transport progress occurs on the TCP side, the bridge mirrors that progress
 * back to the simulator host over USB so the UI can display the authoritative
 * `ServerLiveInteger` and `ClientLiveInteger` values.
 *
 * @param[in] message_type Mirrored message type for the simulator host.
 * @param[in] payload_text Optional UTF-8 payload text.
 */
static void bridge_send_unsolicited_usb_frame(
    bridge_message_type_t message_type,
    const char *payload_text)
{
    bridge_frame_t synthetic_frame = {
        .message_type = message_type,
        .host_live_integer = s_server_live_integer,
        .device_live_integer = s_client_live_integer,
        .sequence = s_usb_sequence++,
        .payload_length = 0U,
    };

    bridge_send_frame(BRIDGE_TRANSPORT_USB, message_type, &synthetic_frame, payload_text);
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
    esp_log_level_set(TAG, ESP_LOG_INFO);

    return usb_serial_jtag_driver_install(&driver_config);
}

/**
 * @brief Start a visible SoftAP that matches the simulator transport profile.
 *
 * @details The ESP32-S3 client scans for `EyalSimulatorAP` before association.
 * The bridge firmware must therefore advertise a discoverable SoftAP with the
 * mirrored simulator credentials and keep `ssid_hidden = 0`.
 *
 * @return
 *      - ESP_OK on success
 *      - ESP_ERR_* if NVS, netif, event-loop, or Wi-Fi startup fails
 */
static esp_err_t bridge_wifi_init_softap(void)
{
    esp_err_t ret = ESP_OK;

    if (!s_wifi_stack_initialized) {
        ret = nvs_flash_init();
        if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
            ESP_ERROR_CHECK(nvs_flash_erase());
            ret = nvs_flash_init();
        }
        if (ret != ESP_OK) {
            return ret;
        }

        ret = esp_netif_init();
        if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
            return ret;
        }

        ret = esp_event_loop_create_default();
        if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
            return ret;
        }

        if (esp_netif_create_default_wifi_ap() == NULL) {
            return ESP_FAIL;
        }

        wifi_init_config_t wifi_init_cfg = WIFI_INIT_CONFIG_DEFAULT();
        ret = esp_wifi_init(&wifi_init_cfg);
        if (ret != ESP_OK) {
            return ret;
        }

        ret = esp_wifi_set_storage(WIFI_STORAGE_RAM);
        if (ret != ESP_OK) {
            return ret;
        }

        s_wifi_stack_initialized = true;
    }

    wifi_config_t wifi_cfg = {0};
    memcpy(wifi_cfg.ap.ssid, BRIDGE_WIFI_SSID, sizeof(BRIDGE_WIFI_SSID) - 1U);
    memcpy(wifi_cfg.ap.password, BRIDGE_WIFI_PASSWORD, sizeof(BRIDGE_WIFI_PASSWORD) - 1U);
    wifi_cfg.ap.ssid_len = sizeof(BRIDGE_WIFI_SSID) - 1U;
    wifi_cfg.ap.channel = BRIDGE_WIFI_CHANNEL;
    wifi_cfg.ap.max_connection = BRIDGE_WIFI_MAX_CONNECTIONS;
    wifi_cfg.ap.authmode = WIFI_AUTH_WPA2_PSK;
    wifi_cfg.ap.ssid_hidden = 0;
    wifi_cfg.ap.pmf_cfg.required = false;

    ret = esp_wifi_set_mode(WIFI_MODE_AP);
    if (ret != ESP_OK) {
        return ret;
    }

    ret = esp_wifi_set_config(WIFI_IF_AP, &wifi_cfg);
    if (ret != ESP_OK) {
        return ret;
    }

    ret = esp_wifi_start();
    if (ret == ESP_OK) {
        s_wifi_transport_enabled = true;
    }
    return ret;
}

/**
 * @brief Close the active TCP listener used by the SoftAP transport.
 *
 * @details Wi-Fi disable tears down both the AP and its listener so the client
 * can no longer discover or connect to the server-side endpoint.
 */
static void bridge_tcp_server_deinit(void)
{
    if (s_tcp_listen_fd >= 0) {
        close(s_tcp_listen_fd);
        s_tcp_listen_fd = -1;
    }
}

/**
 * @brief Stop the SoftAP and close active transport sockets.
 *
 * @details The simulator Wi-Fi toggle must affect the real ESP32-C3 transport
 * endpoint, not just host-side bookkeeping. Stopping the AP and listener makes
 * the client lose the low-level server path immediately.
 */
static void bridge_wifi_disable_transport(void)
{
    bridge_close_tcp_client();
    bridge_tcp_server_deinit();
    if (s_wifi_stack_initialized) {
        (void)esp_wifi_stop();
    }
    s_wifi_transport_enabled = false;
    bridge_enter_state(BRIDGE_STATE_RESET);
}

/**
 * @brief Ensure the bridge SoftAP and TCP listener are available.
 *
 * @details Re-enables the real ESP32-C3 transport endpoint after a simulator
 * Wi-Fi disable request.
 *
 * @return
 *      - ESP_OK on success
 *      - ESP_ERR_* if Wi-Fi or TCP listener startup fails
 */
static esp_err_t bridge_wifi_enable_transport(void)
{
    esp_err_t ret = bridge_wifi_init_softap();
    if (ret != ESP_OK) {
        return ret;
    }

    if (s_tcp_listen_fd < 0) {
        ret = bridge_tcp_server_init();
        if (ret != ESP_OK) {
            return ret;
        }
    }

    s_wifi_transport_enabled = true;
    return ESP_OK;
}

/**
 * @brief Open the bridge TCP listener on the SoftAP-side endpoint.
 *
 * @details The ESP32-S3 client connects to `192.168.4.1:3333` after Wi-Fi
 * association. The bridge therefore owns a single-client TCP server on the
 * SoftAP interface and feeds the same framed transport protocol over that
 * socket.
 *
 * @return
 *      - ESP_OK on success
 *      - ESP_FAIL if the listener cannot be created, bound, or listened
 */
static esp_err_t bridge_tcp_server_init(void)
{
    struct sockaddr_in listen_addr = {
        .sin_family = AF_INET,
        .sin_port = htons(BRIDGE_TCP_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    int reuse = 1;

    s_tcp_listen_fd = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    if (s_tcp_listen_fd < 0) {
        return ESP_FAIL;
    }

    (void)setsockopt(s_tcp_listen_fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    if (bind(s_tcp_listen_fd, (struct sockaddr *)&listen_addr, sizeof(listen_addr)) != 0) {
        close(s_tcp_listen_fd);
        s_tcp_listen_fd = -1;
        return ESP_FAIL;
    }

    if (listen(s_tcp_listen_fd, 1) != 0) {
        close(s_tcp_listen_fd);
        s_tcp_listen_fd = -1;
        return ESP_FAIL;
    }

    return ESP_OK;
}

/**
 * @brief Close the active TCP client and clear the stream buffer.
 *
 * @details Keeps the bridge in a clean single-client state whenever the Wi-Fi
 * transport peer disconnects or a malformed frame is detected.
 */
static void bridge_close_tcp_client(void)
{
    if (s_tcp_client_fd >= 0) {
        ESP_LOGW(TAG, "Closing TCP client socket");
        shutdown(s_tcp_client_fd, 0);
        close(s_tcp_client_fd);
        s_tcp_client_fd = -1;
    }
    s_last_tcp_activity_us = 0;
    s_last_keepalive_tx_us = 0;
    s_keepalive_response_pending = false;
    s_keepalive_window_has_message = false;
    s_keepalive_window_started_us = 0;
    s_keepalive_empty_window_count = 0U;
    s_tcp_sequence = 0U;
    s_running_integer_retry_count = 0U;
    s_active_session_id = 0U;
    s_pending_keepalive_request_id = 0U;
    s_last_completed_keepalive_request_id = 0U;
    s_next_keepalive_request_id = 1U;
    s_tcp_rx_length = 0U;
}

/**
 * @brief Report one low-level bridge fault to the simulator host over USB.
 *
 * @details The Python simulator should mirror authoritative bridge/runtime
 * faults instead of inventing timing failures from UI polling cadence.
 *
 * @param[in] payload_text Short ASCII fault reason.
 */
static void bridge_notify_usb_fault(const char *payload_text)
{
    bridge_send_unsolicited_usb_frame(BRIDGE_MESSAGE_ERROR, payload_text);
}

/**
 * @brief Transmit the current authoritative server keepalive to the client.
 *
 * @details The bridge owns keepalive initiation. It sends the current
 * `ServerLiveInteger` and latest validated `ClientLiveInteger` together with
 * the active `session_id` and keepalive `req_id`, then waits for the client to
 * return a correlated keepalive response.
 *
 * @param[in] reuse_pending_request_id
 *            `true` to resend the current pending request after a timeout;
 *            `false` to create and send a new request id.
 */
static void bridge_send_server_keepalive(bool reuse_pending_request_id)
{
    bridge_frame_t synthetic_keepalive = {0};
    char keepalive_payload[BRIDGE_FRAME_MAX_PAYLOAD + 1U] = {0};
    uint32_t request_id = 0U;

    if (s_tcp_client_fd < 0 || s_active_session_id == 0U) {
        return;
    }

    if (reuse_pending_request_id && s_pending_keepalive_request_id != 0U) {
        request_id = s_pending_keepalive_request_id;
    } else {
        request_id = s_next_keepalive_request_id++;
        s_pending_keepalive_request_id = request_id;
    }

    bridge_build_keepalive_payload(
        keepalive_payload,
        sizeof(keepalive_payload),
        s_active_session_id,
        request_id);
    bridge_send_frame(BRIDGE_TRANSPORT_TCP, BRIDGE_MESSAGE_KEEPALIVE, &synthetic_keepalive, keepalive_payload);
    bridge_send_unsolicited_usb_frame(BRIDGE_MESSAGE_KEEPALIVE, keepalive_payload);
    s_last_keepalive_tx_us = esp_timer_get_time();
    s_keepalive_response_pending = true;
    s_keepalive_empty_window_count = 0U;
    bridge_start_keepalive_window(false);
}

/**
 * @brief Start one buffered keepalive wait window for the TCP response path.
 *
 * @details The bridge waits 0.3 seconds and then checks whether at least one
 * keepalive response arrived in the window. Normal operation keeps staged TCP
 * bytes intact to avoid dropping delayed responses.
 *
 * @param[in] clear_transport_buffer Whether to clear staged TCP RX bytes.
 */
static void bridge_start_keepalive_window(bool clear_transport_buffer)
{
    if (clear_transport_buffer) {
        s_tcp_rx_length = 0U;
    }

    s_keepalive_window_has_message = false;
    s_keepalive_window_started_us = esp_timer_get_time();
}

/**
 * @brief Mark that a keepalive response arrived in the active wait window.
 *
 * @details A non-empty wait window clears the empty-window timeout counter so
 * running-integer supervision can continue normally.
 */
static void bridge_mark_keepalive_window_message(void)
{
    s_keepalive_window_has_message = true;
    s_keepalive_empty_window_count = 0U;
}

/**
 * @brief Emit one detailed state snapshot for the interim-debug stage.
 *
 * @details Logs elapsed reset-to-debug time together with retry/timeout
 * counters so unstable bring-up can be diagnosed from textual ESP logs.
 */
static void bridge_log_interim_debug_state(void)
{
    int64_t now_us = esp_timer_get_time();
    uint32_t reset_to_debug_ms = 0U;

    if (s_interim_debug_logged || s_bridge_state != BRIDGE_STATE_DEBUG_INTERIM) {
        return;
    }

    if (s_reset_cycle_started_us > 0 && now_us >= s_reset_cycle_started_us) {
        reset_to_debug_ms = (uint32_t)((now_us - s_reset_cycle_started_us) / 1000LL);
    }

    ESP_LOGI(TAG,
             "InterimDebug details: reset_to_debug_ms=%" PRIu32
             ", running_retries=%" PRIu32
             ", timeout_events=%" PRIu32
             ", tcp_client=%s",
             reset_to_debug_ms,
             s_running_integer_retry_count,
             s_timeout_event_count,
             (s_tcp_client_fd >= 0) ? "connected" : "disconnected");
    s_interim_debug_logged = true;
}

/**
 * @brief Process the bridge interim-debug state and promote into keepalive.
 *
 * @details This debug-only gate logs transition telemetry once, then starts
 * keepalive supervision only after both peers finished the connect handshake.
 */
static void bridge_service_debug_interim_state(void)
{
    if (s_bridge_state != BRIDGE_STATE_DEBUG_INTERIM) {
        return;
    }

    bridge_log_interim_debug_state();
    if (s_tcp_client_fd < 0) {
        bridge_enter_state(BRIDGE_STATE_INITIALIZE);
        return;
    }

    s_keepalive_response_pending = false;
    s_last_keepalive_tx_us = 0;
    bridge_enter_state(BRIDGE_STATE_KEEPALIVE);
    bridge_send_server_keepalive(false);
}

/**
 * @brief Retry or tear down after a running-integer validation failure.
 *
 * @details Both peers get up to three retries for counter mismatch/timeouts.
 * While retries remain, the bridge keeps the TCP session open and resends the
 * current keepalive request id without reseeding counters.
 * After the third failure, the bridge closes the TCP session and returns to
 * initialize wait.
 *
 * @param[in] fault_reason Short ASCII reason text.
 */
static void bridge_handle_running_integer_failure(const char *fault_reason)
{
    const char *reason_text = (fault_reason != NULL) ? fault_reason : "running_integer_failure";

    s_running_integer_retry_count++;
    if (s_running_integer_retry_count < BRIDGE_RUNNING_INTEGER_RETRY_LIMIT && s_tcp_client_fd >= 0) {
        ESP_LOGW(TAG,
                 "Running-integer retry (%" PRIu32 "/%" PRIu32 "): %s",
                 s_running_integer_retry_count,
                 BRIDGE_RUNNING_INTEGER_RETRY_LIMIT,
                 reason_text);
        bridge_notify_usb_fault(reason_text);
        s_keepalive_response_pending = false;
        s_keepalive_window_has_message = false;
        s_keepalive_window_started_us = 0;
        s_keepalive_empty_window_count = 0U;
        s_last_keepalive_tx_us = 0;
        bridge_enter_state(BRIDGE_STATE_KEEPALIVE);
        bridge_send_server_keepalive(true);
        return;
    }

    ESP_LOGE(TAG,
             "Running-integer retries exhausted (%" PRIu32 "/%" PRIu32 "): %s",
             s_running_integer_retry_count,
             BRIDGE_RUNNING_INTEGER_RETRY_LIMIT,
             reason_text);
    bridge_notify_usb_fault("keepalive_retry_exhausted");
    bridge_close_tcp_client();
    bridge_enter_state(BRIDGE_STATE_INITIALIZE);
    s_server_live_integer = 0U;
    s_client_live_integer = 0U;
    s_tcp_sequence = 0U;
    s_running_integer_retry_count = 0U;
}

/**
 * @brief Pace bridge-owned keepalive initiation at the configured cadence.
 *
 * @details Once a TCP client is attached and the previous keepalive exchange
 * has completed successfully, the bridge sends the next authoritative
 * keepalive after the fixed period. This avoids using unrelated TCP activity
 * as a proxy for transport liveness.
 */
static void bridge_service_keepalive_engine(void)
{
    int64_t now_us = 0;

    if (s_tcp_client_fd < 0 ||
        s_keepalive_response_pending ||
        s_bridge_state != BRIDGE_STATE_KEEPALIVE) {
        return;
    }

    now_us = esp_timer_get_time();
    if (s_last_keepalive_tx_us != 0 &&
        (now_us - s_last_keepalive_tx_us) < ((int64_t)BRIDGE_KEEPALIVE_PERIOD_MS * 1000LL)) {
        return;
    }

    bridge_send_server_keepalive(false);
}

/**
 * @brief Enforce the low-level TCP keepalive watchdog inside the bridge.
 *
 * @details The ESP32-C3 owns the real TCP session with the client and must be
 * the source of truth for connection-loss detection. For each pending
 * keepalive request id, the bridge tracks one 0.3-second response deadline and
 * retries the same request id until retries are exhausted.
 */
static void bridge_service_transport_watchdog(void)
{
    if (s_tcp_client_fd < 0 ||
        !s_keepalive_response_pending ||
        s_last_keepalive_tx_us <= 0 ||
        s_bridge_state != BRIDGE_STATE_KEEPALIVE) {
        return;
    }

    if (s_keepalive_window_started_us <= 0) {
        bridge_start_keepalive_window(false);
        return;
    }

    uint32_t window_elapsed_ms =
        (uint32_t)((esp_timer_get_time() - s_keepalive_window_started_us) / 1000LL);
    if (window_elapsed_ms < BRIDGE_KEEPALIVE_WAIT_WINDOW_MS) {
        return;
    }

    s_timeout_event_count++;
    ESP_LOGW(TAG,
             "Keepalive response timeout: sid=%" PRIu32 " req=%" PRIu32 " after %u ms",
             s_active_session_id,
             s_pending_keepalive_request_id,
             BRIDGE_KEEPALIVE_WAIT_WINDOW_MS);
    bridge_handle_running_integer_failure("keepalive_response_timeout");
}

/**
 * @brief Update the bridge state from one validated request frame.
 *
 * @details The bridge accepts the same framed request types over either USB or
 * TCP. TCP responses use `client_connected` on the connect step so the client
 * ESP32-S3 can promote from `CONNECT` into `KEEPALIVE`.
 *
 * @param[in] transport Source transport that should receive the response.
 * @param[in] frame Parsed request frame.
 */
static void bridge_handle_frame(bridge_transport_t transport, const bridge_frame_t *frame)
{
    char payload_text[BRIDGE_FRAME_MAX_PAYLOAD + 1U] = {0};

    if (frame == NULL) {
        return;
    }

    if (frame->payload_length > 0U) {
        memcpy(payload_text, frame->payload, frame->payload_length);
        payload_text[frame->payload_length] = '\0';
    }

    switch (frame->message_type) {
    case BRIDGE_MESSAGE_RESET:
        bridge_enter_state(BRIDGE_STATE_RESET);
        s_server_live_integer = 0U;
        s_client_live_integer = 0U;
        s_tcp_sequence = 0U;
        s_running_integer_retry_count = 0U;
        s_last_keepalive_tx_us = 0;
        s_keepalive_response_pending = false;
        s_keepalive_window_has_message = false;
        s_keepalive_window_started_us = 0;
        s_keepalive_empty_window_count = 0U;
        s_active_session_id = 0U;
        s_pending_keepalive_request_id = 0U;
        s_last_completed_keepalive_request_id = 0U;
        s_next_keepalive_request_id = 1U;
        bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "reset_ack");
        bridge_close_tcp_client();
        break;
    case BRIDGE_MESSAGE_INITIALIZE:
        bridge_enter_state(BRIDGE_STATE_INITIALIZE);
        s_tcp_sequence = 0U;
        s_running_integer_retry_count = 0U;
        s_keepalive_window_has_message = false;
        s_keepalive_window_started_us = 0;
        s_keepalive_empty_window_count = 0U;
        s_active_session_id = 0U;
        s_pending_keepalive_request_id = 0U;
        s_last_completed_keepalive_request_id = 0U;
        s_next_keepalive_request_id = 1U;
        bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "initialize_ack");
        break;
    case BRIDGE_MESSAGE_CONNECT:
    {
        char connect_payload[BRIDGE_FRAME_MAX_PAYLOAD + 1U] = {0};
        bridge_enter_state(BRIDGE_STATE_CONNECT);
        s_server_live_integer = 0U;
        s_client_live_integer = BRIDGE_DEVICE_LIVE_START;
        s_tcp_sequence = 0U;
        s_running_integer_retry_count = 0U;
        s_last_tcp_activity_us = (s_tcp_client_fd >= 0) ? esp_timer_get_time() : 0;
        s_last_keepalive_tx_us = 0;
        s_keepalive_response_pending = false;
        s_keepalive_window_has_message = false;
        s_keepalive_window_started_us = 0;
        s_keepalive_empty_window_count = 0U;
        s_active_session_id = s_next_session_id++;
        s_pending_keepalive_request_id = 0U;
        s_last_completed_keepalive_request_id = 0U;
        s_next_keepalive_request_id = 1U;
        snprintf(connect_payload,
                 sizeof(connect_payload),
                 "client_connected;sid=%" PRIu32,
                 s_active_session_id);
        if (transport == BRIDGE_TRANSPORT_TCP) {
            bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, connect_payload);
            bridge_send_unsolicited_usb_frame(BRIDGE_MESSAGE_ACK, connect_payload);
            bridge_enter_state(BRIDGE_STATE_DEBUG_INTERIM);
        } else {
            bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, connect_payload);
        }
        if (transport == BRIDGE_TRANSPORT_USB && s_tcp_client_fd >= 0) {
            bridge_send_unsolicited_usb_frame(BRIDGE_MESSAGE_ACK, connect_payload);
            bridge_enter_state(BRIDGE_STATE_DEBUG_INTERIM);
        }
        break;
    }
    case BRIDGE_MESSAGE_DISCONNECT:
        bridge_enter_state(BRIDGE_STATE_DISCONNECT);
        bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "disconnect_ack");
        if (transport == BRIDGE_TRANSPORT_TCP) {
            bridge_close_tcp_client();
        }
        break;
    case BRIDGE_MESSAGE_KEEPALIVE:
        if (transport == BRIDGE_TRANSPORT_TCP) {
            uint32_t payload_session_id = 0U;
            uint32_t payload_request_id = 0U;
            bool has_session_id = bridge_try_parse_u32_payload_value(payload_text, "sid", &payload_session_id);
            bool has_request_id = bridge_try_parse_u32_payload_value(payload_text, "req", &payload_request_id);
            uint32_t expected_client_live_integer = s_server_live_integer + 1U;

            if (!s_keepalive_response_pending || s_pending_keepalive_request_id == 0U) {
                ESP_LOGW(TAG,
                         "Ignoring unexpected keepalive response with no pending request (sid=%" PRIu32 ", req=%" PRIu32 ")",
                         payload_session_id,
                         payload_request_id);
                break;
            }
            if (!has_session_id || !has_request_id) {
                bridge_handle_running_integer_failure("keepalive_metadata_missing");
                break;
            }
            if (payload_session_id != s_active_session_id) {
                ESP_LOGW(TAG,
                         "Ignoring stale keepalive session response sid=%" PRIu32 " active=%" PRIu32,
                         payload_session_id,
                         s_active_session_id);
                break;
            }
            if (payload_request_id != s_pending_keepalive_request_id) {
                ESP_LOGW(TAG,
                         "Ignoring stale keepalive request response req=%" PRIu32 " pending=%" PRIu32,
                         payload_request_id,
                         s_pending_keepalive_request_id);
                break;
            }
            if (frame->host_live_integer != s_server_live_integer ||
                frame->device_live_integer != expected_client_live_integer) {
                bridge_send_frame(transport, BRIDGE_MESSAGE_ERROR, frame, "keepalive_counter_mismatch");
                bridge_handle_running_integer_failure("keepalive_counter_mismatch");
                break;
            }

            s_last_tcp_activity_us = esp_timer_get_time();
            s_client_live_integer = frame->device_live_integer;
            s_server_live_integer = s_client_live_integer + 1U;
            bridge_mark_keepalive_window_message();
            s_keepalive_response_pending = false;
            s_keepalive_window_started_us = 0;
            s_pending_keepalive_request_id = 0U;
            s_last_completed_keepalive_request_id = payload_request_id;
            s_running_integer_retry_count = 0U;
            bridge_enter_state(BRIDGE_STATE_KEEPALIVE);
            char keepalive_status_payload[BRIDGE_FRAME_MAX_PAYLOAD + 1U] = {0};
            bridge_build_keepalive_payload(
                keepalive_status_payload,
                sizeof(keepalive_status_payload),
                s_active_session_id,
                payload_request_id);
            bridge_send_unsolicited_usb_frame(BRIDGE_MESSAGE_KEEPALIVE, keepalive_status_payload);
        } else {
            bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "usb_keepalive_not_supported");
        }
        break;
    case BRIDGE_MESSAGE_DATA:
        if (transport == BRIDGE_TRANSPORT_USB && strcmp(payload_text, "wifi_disable") == 0) {
            bridge_wifi_disable_transport();
            bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "wifi_disabled");
        } else if (transport == BRIDGE_TRANSPORT_USB && strcmp(payload_text, "wifi_enable") == 0) {
            esp_err_t ret = bridge_wifi_enable_transport();
            if (ret == ESP_OK) {
                bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "wifi_enabled");
            } else {
                bridge_send_frame(transport, BRIDGE_MESSAGE_ERROR, frame, "wifi_enable_failed");
            }
        } else {
            bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "data_ack");
        }
        break;
    case BRIDGE_MESSAGE_ERROR:
        bridge_enter_state(BRIDGE_STATE_ERROR);
        bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "error_ack");
        break;
    case BRIDGE_MESSAGE_ACK:
    default:
        bridge_send_frame(transport, BRIDGE_MESSAGE_ERROR, frame, "unsupported");
        break;
    }
}

/**
 * @brief Accept a waiting TCP client if the listener is ready.
 *
 * @details The bridge owns one active TCP session at a time. If a new client
 * connects while a stale session is still attached, the stale session is
 * dropped immediately so reconnect attempts from the ESP32-S3 are not blocked.
 */
static void bridge_accept_tcp_client(void)
{
    struct sockaddr_in client_addr = {0};
    socklen_t client_addr_len = sizeof(client_addr);
    int accepted_fd = -1;
    bridge_frame_t synthetic_connect = {0};
    fd_set read_fds;
    struct timeval poll_timeout = {
        .tv_sec = 0,
        .tv_usec = 0,
    };

    if (s_tcp_listen_fd < 0) {
        return;
    }

    FD_ZERO(&read_fds);
    FD_SET(s_tcp_listen_fd, &read_fds);
    if (select(s_tcp_listen_fd + 1, &read_fds, NULL, NULL, &poll_timeout) <= 0) {
        return;
    }

    accepted_fd = accept(s_tcp_listen_fd, (struct sockaddr *)&client_addr, &client_addr_len);
    if (accepted_fd < 0) {
        return;
    }

    if (s_tcp_client_fd >= 0) {
        ESP_LOGW(TAG, "Replacing stale TCP client with a new connection");
        bridge_close_tcp_client();
    }

    s_tcp_client_fd = accepted_fd;
    s_last_tcp_activity_us = esp_timer_get_time();
    s_last_keepalive_tx_us = 0;
    s_tcp_rx_length = 0U;
    bridge_enter_state(BRIDGE_STATE_CONNECT);
    s_server_live_integer = 0U;
    s_client_live_integer = BRIDGE_DEVICE_LIVE_START;
    s_tcp_sequence = 0U;
    s_keepalive_response_pending = false;
    s_keepalive_window_has_message = false;
    s_keepalive_window_started_us = 0;
    s_keepalive_empty_window_count = 0U;
    s_running_integer_retry_count = 0U;
    s_active_session_id = 0U;
    s_pending_keepalive_request_id = 0U;
    s_last_completed_keepalive_request_id = 0U;
    s_next_keepalive_request_id = 1U;
    ESP_LOGI(TAG, "Accepted TCP client");
    bridge_send_frame(BRIDGE_TRANSPORT_TCP, BRIDGE_MESSAGE_ACK, &synthetic_connect, "tcp_connected");
    bridge_send_unsolicited_usb_frame(BRIDGE_MESSAGE_ACK, "tcp_connected");
}

/**
 * @brief Poll the TCP client socket and process complete framed requests.
 *
 * @details TCP is stream-oriented, so the bridge buffers bytes until a whole
 * framed request is available before dispatching it through the shared parser.
 * Accepting the socket alone is not enough to start keepalive traffic; the
 * client must still send its framed `CONNECT` request so both sides reset the
 * live-integer baseline at the same protocol point.
 */
static void bridge_poll_tcp_client(void)
{
    uint8_t temp_buffer[128] = {0};
    fd_set read_fds;
    struct timeval poll_timeout = {
        .tv_sec = 0,
        .tv_usec = 0,
    };

    if (s_tcp_client_fd < 0) {
        return;
    }

    FD_ZERO(&read_fds);
    FD_SET(s_tcp_client_fd, &read_fds);
    if (select(s_tcp_client_fd + 1, &read_fds, NULL, NULL, &poll_timeout) <= 0) {
        return;
    }

    int received = recv(s_tcp_client_fd, temp_buffer, sizeof(temp_buffer), 0);
    if (received > 0) {
        size_t copy_length = (size_t)received;
        if ((s_tcp_rx_length + copy_length) > sizeof(s_tcp_rx_buffer)) {
            bridge_close_tcp_client();
            return;
        }
        memcpy(&s_tcp_rx_buffer[s_tcp_rx_length], temp_buffer, copy_length);
        s_tcp_rx_length += copy_length;
    } else {
        if (received == 0) {
            bridge_close_tcp_client();
        }
        return;
    }

    while (s_tcp_rx_length >= BRIDGE_FRAME_OVERHEAD) {
        uint16_t payload_length = 0U;
        size_t frame_length = 0U;
        bridge_frame_t frame = {0};

        if (s_tcp_rx_buffer[0] != BRIDGE_SOF_BYTE0 || s_tcp_rx_buffer[1] != BRIDGE_SOF_BYTE1) {
            memmove(s_tcp_rx_buffer, &s_tcp_rx_buffer[1], s_tcp_rx_length - 1U);
            s_tcp_rx_length -= 1U;
            continue;
        }

        payload_length = (uint16_t)(s_tcp_rx_buffer[3] | ((uint16_t)s_tcp_rx_buffer[4] << 8));
        frame_length = BRIDGE_FRAME_OVERHEAD + payload_length;
        if (payload_length > BRIDGE_FRAME_MAX_PAYLOAD) {
            ESP_LOGW(TAG, "Closing TCP client: payload too large (%u)", payload_length);
            bridge_close_tcp_client();
            return;
        }
        if (s_tcp_rx_length < frame_length) {
            return;
        }

        if (bridge_parse_frame(s_tcp_rx_buffer, frame_length, &frame) != ESP_OK) {
            ESP_LOGW(TAG, "Closing TCP client: frame parse failed");
            bridge_notify_usb_fault("tcp_frame_parse_failed");
            bridge_close_tcp_client();
            return;
        }

        s_last_tcp_activity_us = esp_timer_get_time();
        bridge_handle_frame(BRIDGE_TRANSPORT_TCP, &frame);
        memmove(s_tcp_rx_buffer, &s_tcp_rx_buffer[frame_length], s_tcp_rx_length - frame_length);
        s_tcp_rx_length -= frame_length;
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
    s_reset_cycle_started_us = esp_timer_get_time();

    if (init_result != ESP_OK) {
        bridge_enter_state(BRIDGE_STATE_ERROR);
        while (1) {
            vTaskDelay(pdMS_TO_TICKS(250));
        }
    }

    init_result = bridge_wifi_init_softap();
    if (init_result != ESP_OK) {
        bridge_enter_state(BRIDGE_STATE_ERROR);
        while (1) {
            vTaskDelay(pdMS_TO_TICKS(250));
        }
    }

    init_result = bridge_tcp_server_init();
    if (init_result != ESP_OK) {
        bridge_enter_state(BRIDGE_STATE_ERROR);
        while (1) {
            vTaskDelay(pdMS_TO_TICKS(250));
        }
    }

    while (1) {
        if (s_wifi_transport_enabled) {
            bridge_accept_tcp_client();
            bridge_poll_tcp_client();
            bridge_service_debug_interim_state();
            bridge_service_keepalive_engine();
            bridge_service_transport_watchdog();
        }

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

        bridge_handle_frame(BRIDGE_TRANSPORT_USB, &frame);
    }
}
