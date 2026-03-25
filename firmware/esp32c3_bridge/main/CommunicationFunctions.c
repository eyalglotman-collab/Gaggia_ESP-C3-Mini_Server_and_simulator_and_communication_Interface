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

#include "CommunicationFunctions.h"

#define BRIDGE_FRAME_MAX_PAYLOAD        2300
#define BRIDGE_TEXT_PAYLOAD_MAX         256
#define BRIDGE_FRAME_OVERHEAD           17
#define BRIDGE_FRAME_MAX_SIZE           (BRIDGE_FRAME_MAX_PAYLOAD + BRIDGE_FRAME_OVERHEAD)
#define BRIDGE_RX_BUFFER_SIZE           (BRIDGE_FRAME_MAX_SIZE * 2)
#define BRIDGE_TX_BUFFER_SIZE           (BRIDGE_FRAME_MAX_SIZE * 2)
#define BRIDGE_POLL_DELAY_MS            20
#define BRIDGE_DEVICE_LIVE_START        1U
#define BRIDGE_WIFI_SSID                "EyalSimulatorAP"
#define BRIDGE_WIFI_PASSWORD            "espresso1234"
#define BRIDGE_WIFI_CHANNEL             1
#define BRIDGE_WIFI_MAX_CONNECTIONS     4
#define BRIDGE_TCP_PORT                 3333
#define BRIDGE_TCP_RX_BUFFER_SIZE       (BRIDGE_FRAME_MAX_SIZE * 3)
#define BRIDGE_KEEPALIVE_PERIOD_MS      300
#define BRIDGE_KEEPALIVE_WAIT_WINDOW_MS 450
#define BRIDGE_RUNNING_INTEGER_RETRY_LIMIT 3U
#define BRIDGE_REALTIME_DATA_PAYLOAD_SIZE_BYTES ((uint16_t)BRIDGE_REALTIME_DATA_PAYLOAD_BYTES)

#define BRIDGE_SOF_BYTE0                0xA5
#define BRIDGE_SOF_BYTE1                0x5A

#define BRIDGE_DATA_MAGIC_DOWNLINK      0xD0U
#define BRIDGE_DATA_MAGIC_UPLINK        0xD1U

static const char *TAG = "bridge";

typedef enum {
    BRIDGE_STATE_RESET = 0,
    BRIDGE_STATE_INITIALIZE = 1,
    BRIDGE_STATE_CONNECT = 2,
    BRIDGE_STATE_KEEPALIVE_SERVER_SEND = 3,
    BRIDGE_STATE_KEEPALIVE_CLIENT_RETURN = 4,
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
static int32_t s_transport_last_delay_ms = -1;
static uint32_t s_transport_max_delay_ms = 0U;
static uint32_t s_total_error_count = 0U;
static int64_t s_keepalive_window_started_us = 0;
static int64_t s_reset_cycle_started_us = 0;
static uint32_t s_realtime_data_sequence = 0U;
static bool s_realtime_data_stream_active = false;
static uint32_t s_peer_data_interface_version = 0U;
static bool s_peer_data_interface_version_valid = false;

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
                                           const char *tag_text,
                                           uint32_t session_id,
                                           uint32_t request_id);
static void bridge_send_server_keepalive(bool reuse_pending_request_id);
static void bridge_start_keepalive_window(bool clear_transport_buffer);
static void bridge_mark_keepalive_window_message(void);
static void bridge_service_keepalive_engine(void);
static void bridge_service_transport_watchdog(void);
static void bridge_handle_running_integer_failure(const char *fault_reason);
static void bridge_record_error(const char *reason_text);
static uint32_t bridge_compute_pending_keepalive_delay_ms(void);
static void bridge_reset_telemetry_counters(void);
static void bridge_reset_transport_max_delay(void);
static void bridge_update_transport_last_delay_for_state(bridge_state_t state);
static void bridge_send_telemetry_update_usb(void);
static void bridge_send_unsolicited_usb_frame(bridge_message_type_t message_type,
                                              const char *payload_text);
static void bridge_send_frame_binary(bridge_transport_t transport,
                                     bridge_message_type_t message_type,
                                     const bridge_frame_t *request_frame,
                                     const uint8_t *payload_data,
                                     size_t payload_length);
static bool bridge_is_keepalive_session_active(void);
static void bridge_notify_realtime_data_halted(const char *reason_text);
static void bridge_send_realtime_data_packet(void);

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
    case BRIDGE_STATE_KEEPALIVE_SERVER_SEND:
        return "USB_TransportKeepAlive_ServerSend";
    case BRIDGE_STATE_KEEPALIVE_CLIENT_RETURN:
        return "USB_TransportKeepAlive_ClientReturn";
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
 * @details Records reset-cycle start timestamps and clears keepalive/session
 * bookkeeping whenever the state machine re-enters reset.
 *
 * @param[in] next_state New state to enter.
 */
static void bridge_enter_state(bridge_state_t next_state)
{
    bridge_state_t previous_state = s_bridge_state;
    bool state_changed = (s_bridge_state != next_state);
    bool previous_keepalive = (previous_state == BRIDGE_STATE_KEEPALIVE_SERVER_SEND ||
                               previous_state == BRIDGE_STATE_KEEPALIVE_CLIENT_RETURN);
    bool next_keepalive = (next_state == BRIDGE_STATE_KEEPALIVE_SERVER_SEND ||
                           next_state == BRIDGE_STATE_KEEPALIVE_CLIENT_RETURN);

    if (state_changed) {
        ESP_LOGI(TAG,
                 "State %s -> %s",
                 bridge_state_to_string(previous_state),
                 bridge_state_to_string(next_state));
    }

    if (state_changed && previous_keepalive && !next_keepalive && s_realtime_data_stream_active) {
        bridge_notify_realtime_data_halted("not_in_keepalive_state");
        s_realtime_data_stream_active = false;
    }
    if (state_changed && previous_keepalive && !next_keepalive) {
        /* Flush staged TCP bytes from the previous session to avoid replaying
         * stale DATA frames after reconnect/state recovery. */
        s_tcp_rx_length = 0U;
    }

    s_bridge_state = next_state;
    bridge_update_transport_last_delay_for_state(next_state);
    if (state_changed) {
        bridge_send_telemetry_update_usb();
    }
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
        s_realtime_data_sequence = 0U;
        s_realtime_data_stream_active = false;
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
 * @brief Return whether DATA forwarding is allowed for the active link state.
 *
 * @details DATA is valid only during keepalive phases with a non-zero session
 * id. Dropping out-of-session DATA prevents stale buffered packets from a
 * prior TCP link from leaking into the new handshake.
 */
static bool bridge_is_keepalive_session_active(void)
{
    return (s_active_session_id != 0U) &&
           (s_bridge_state == BRIDGE_STATE_KEEPALIVE_SERVER_SEND ||
            s_bridge_state == BRIDGE_STATE_KEEPALIVE_CLIENT_RETURN);
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
 * @brief Serialize one 16-bit unsigned integer in little-endian order.
 *
 * @param[out] destination Destination byte pointer.
 * @param[in] value Unsigned value to serialize.
 */
static void bridge_write_u16_le(uint8_t *destination, uint16_t value)
{
    if (destination == NULL) {
        return;
    }

    destination[0] = (uint8_t)(value & 0xFFU);
    destination[1] = (uint8_t)((value >> 8) & 0xFFU);
}

/**
 * @brief Serialize one 32-bit unsigned integer in little-endian order.
 *
 * @param[out] destination Destination byte pointer.
 * @param[in] value Unsigned value to serialize.
 */
static void bridge_write_u32_le(uint8_t *destination, uint32_t value)
{
    if (destination == NULL) {
        return;
    }

    destination[0] = (uint8_t)(value & 0xFFU);
    destination[1] = (uint8_t)((value >> 8) & 0xFFU);
    destination[2] = (uint8_t)((value >> 16) & 0xFFU);
    destination[3] = (uint8_t)((value >> 24) & 0xFFU);
}

/**
 * @brief Increment the aggregate telemetry error counter.
 *
 * @details The simulator UI uses this firmware-owned counter as the
 * authoritative total of low-level and top-layer communication faults.
 *
 * @param[in] reason_text Optional reason string for future diagnostics.
 */
static void bridge_record_error(const char *reason_text)
{
    (void)reason_text;
    s_total_error_count++;
}

/**
 * @brief Compute the keepalive round-trip delay of the pending request.
 *
 * @details Delay is measured on the ESP32-C3 from keepalive request
 * transmission to a validated matching client response.
 *
 * @return Delay in milliseconds, clamped to zero for invalid timestamps.
 */
static uint32_t bridge_compute_pending_keepalive_delay_ms(void)
{
    int64_t now_us = 0;
    int64_t delta_us = 0;

    if (s_last_keepalive_tx_us <= 0) {
        return 0U;
    }

    now_us = esp_timer_get_time();
    delta_us = now_us - s_last_keepalive_tx_us;
    if (delta_us < 0) {
        delta_us = 0;
    }

    if (delta_us == 0) {
        return 0U;
    }

    return (uint32_t)((delta_us + 999LL) / 1000LL);
}

/**
 * @brief Clear bridge-owned telemetry counters.
 *
 * @details Operators can reset telemetry without resetting the transport state
 * machine by issuing `DATA telemetry_reset` from the simulator host.
 */
static void bridge_reset_telemetry_counters(void)
{
    bridge_update_transport_last_delay_for_state(s_bridge_state);
    s_transport_max_delay_ms = 0U;
    s_total_error_count = 0U;
}

/**
 * @brief Clear only the bridge maximum-delay telemetry value.
 *
 * @details The operator UI uses this to restart max-delay tracking without
 * erasing the current `last delay` sample or total error history.
 */
static void bridge_reset_transport_max_delay(void)
{
    s_transport_max_delay_ms = 0U;
}

/**
 * @brief Keep last-delay semantics aligned with the active TopLayer state.
 *
 * @details The controller is authoritative for transport delay telemetry.
 * Outside keepalive phases, `Transport Last Delay` is explicitly reported as
 * `-1` so the host can distinguish "no keepalive exchange active" from a
 * valid measured delay.
 *
 * @param[in] state Bridge runtime state to evaluate.
 */
static void bridge_update_transport_last_delay_for_state(bridge_state_t state)
{
    if (state == BRIDGE_STATE_KEEPALIVE_SERVER_SEND || state == BRIDGE_STATE_KEEPALIVE_CLIENT_RETURN) {
        /* Keepalive phases preserve the previous value until a real response
         * measurement updates it, avoiding synthetic zero-delay samples. */
        return;
    }

    s_transport_last_delay_ms = -1;
}

/**
 * @brief Emit one USB telemetry snapshot payload with delay and error counters.
 *
 * @details Host-side dashboards consume this payload to reflect controller
 * telemetry updates immediately, including `-1` delay when outside keepalive.
 */
static void bridge_send_telemetry_update_usb(void)
{
    char telemetry_payload[BRIDGE_TEXT_PAYLOAD_MAX + 1U] = {0};

    snprintf(
        telemetry_payload,
        sizeof(telemetry_payload),
        "telemetry;td_last_ms=%" PRId32 ";td_max_ms=%" PRIu32 ";terr=%" PRIu32,
        s_transport_last_delay_ms,
        s_transport_max_delay_ms,
        s_total_error_count);

    bridge_send_unsolicited_usb_frame(BRIDGE_MESSAGE_DATA, telemetry_payload);
}

/**
 * @brief Build one keepalive payload with correlation metadata.
 *
 * @details The bridge uses `ka_req` for server-issued keepalive requests and
 * `ka_resp` when mirroring a validated client response to the simulator host.
 * The same payload also carries firmware-owned telemetry fields for delay and
 * total error counters.
 *
 * @param[out] buffer Destination payload buffer.
 * @param[in] buffer_length Destination buffer size in bytes.
 * @param[in] tag_text Keepalive payload tag (`ka_req` or `ka_resp`).
 * @param[in] session_id Active bridge session id.
 * @param[in] request_id Keepalive request id for this exchange.
 */
static void bridge_build_keepalive_payload(char *buffer,
                                           size_t buffer_length,
                                           const char *tag_text,
                                           uint32_t session_id,
                                           uint32_t request_id)
{
    if (buffer == NULL || buffer_length == 0U) {
        return;
    }

    snprintf(buffer,
             buffer_length,
             "%s;sid=%" PRIu32 ";req=%" PRIu32 ";rver=%u;td_last_ms=%" PRId32 ";td_max_ms=%" PRIu32 ";terr=%" PRIu32,
             (tag_text != NULL) ? tag_text : "ka_req",
             session_id,
             request_id,
             (unsigned)BRIDGE_REALTIME_DATA_INTERFACE_VERSION,
             s_transport_last_delay_ms,
             s_transport_max_delay_ms,
             s_total_error_count);
}

/**
 * @brief Encode and transmit one framed response with raw payload bytes.
 *
 * @details Replies always use the same frame envelope as the PC host. This
 * helper is used for both text payloads and fixed-size binary realtime data.
 *
 * @param[in] transport Destination transport.
 * @param[in] message_type Response packet type.
 * @param[in] request_frame Source request used for host counter and sequence.
 * @param[in] payload_data Optional payload byte buffer.
 * @param[in] payload_length Number of payload bytes.
 */
static void bridge_send_frame_binary(
    bridge_transport_t transport,
    bridge_message_type_t message_type,
    const bridge_frame_t *request_frame,
    const uint8_t *payload_data,
    size_t payload_length)
{
    uint8_t frame[BRIDGE_FRAME_MAX_SIZE] = {0};
    size_t frame_length = 0U;
    uint16_t crc = 0U;
    uint16_t tx_sequence = 0U;

    if (request_frame == NULL) {
        return;
    }

    if (payload_length > BRIDGE_FRAME_MAX_PAYLOAD) {
        payload_length = BRIDGE_FRAME_MAX_PAYLOAD;
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

    if (payload_length > 0U && payload_data != NULL) {
        memcpy(&frame[15], payload_data, payload_length);
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
 * @brief Encode and transmit one framed response with UTF-8 payload text.
 *
 * @param[in] transport Destination transport.
 * @param[in] message_type Response packet type.
 * @param[in] request_frame Source request used for host counter and sequence.
 * @param[in] payload_text Optional NUL-terminated payload text.
 */
static void bridge_send_frame(
    bridge_transport_t transport,
    bridge_message_type_t message_type,
    const bridge_frame_t *request_frame,
    const char *payload_text)
{
    size_t payload_length = 0U;
    const uint8_t *payload_data = NULL;

    if (payload_text != NULL) {
        payload_length = strnlen(payload_text, BRIDGE_FRAME_MAX_PAYLOAD);
        payload_data = (const uint8_t *)payload_text;
    }

    bridge_send_frame_binary(transport, message_type, request_frame, payload_data, payload_length);
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
 * @brief Notify the simulator host that realtime streaming halted.
 *
 * @details The controller treats loss of keepalive as a critical condition.
 * For now this is surfaced as a bridge error frame to the simulator UI.
 *
 * @param[in] reason_text Short halt reason.
 */
static void bridge_notify_realtime_data_halted(const char *reason_text)
{
    char payload_text[BRIDGE_TEXT_PAYLOAD_MAX + 1U] = {0};

    snprintf(payload_text,
             sizeof(payload_text),
             "realtime_data_halted;reason=%s;sid=%" PRIu32,
             (reason_text != NULL) ? reason_text : "not_in_keepalive_state",
             s_active_session_id);
    bridge_notify_usb_fault(payload_text);
}

/**
 * @brief Send one fixed-size realtime data frame to the TCP client.
 *
 * @details The payload contract is binary and deterministic:
 * version (u32), data sequence (u32), data-byte-count (u16), then 20 doubles.
 * Frames are sent only while the bridge is in keepalive states.
 */
static void bridge_send_realtime_data_packet(void)
{
    uint8_t payload[BRIDGE_REALTIME_DATA_PAYLOAD_BYTES] = {0};
    bridge_frame_t synthetic_frame = {0};
    uint16_t value_bytes = (uint16_t)(BRIDGE_REALTIME_TEMP_COUNT * sizeof(double));
    uint32_t data_sequence = 0U;

    if (s_tcp_client_fd < 0) {
        return;
    }

    if (s_bridge_state != BRIDGE_STATE_KEEPALIVE_SERVER_SEND &&
        s_bridge_state != BRIDGE_STATE_KEEPALIVE_CLIENT_RETURN) {
        if (s_realtime_data_stream_active) {
            bridge_notify_realtime_data_halted("not_in_keepalive_state");
            s_realtime_data_stream_active = false;
        }
        return;
    }

    data_sequence = s_realtime_data_sequence++;
    bridge_write_u32_le(&payload[0], BRIDGE_REALTIME_DATA_INTERFACE_VERSION);
    bridge_write_u32_le(&payload[4], data_sequence);
    bridge_write_u16_le(&payload[8], value_bytes);

    for (uint32_t index = 0U; index < BRIDGE_REALTIME_TEMP_COUNT; ++index) {
        double sample_value = 85.0 + (double)index + ((double)(data_sequence % 200U) * 0.01);
        memcpy(&payload[10U + (index * sizeof(double))], &sample_value, sizeof(double));
    }

    synthetic_frame.message_type = BRIDGE_MESSAGE_DATA;
    synthetic_frame.host_live_integer = s_server_live_integer;
    synthetic_frame.device_live_integer = s_client_live_integer;
    synthetic_frame.sequence = s_tcp_sequence;
    synthetic_frame.payload_length = BRIDGE_REALTIME_DATA_PAYLOAD_SIZE_BYTES;
    bridge_send_frame_binary(
        BRIDGE_TRANSPORT_TCP,
        BRIDGE_MESSAGE_DATA,
        &synthetic_frame,
        payload,
        sizeof(payload));
    s_realtime_data_stream_active = true;
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
    s_peer_data_interface_version = 0U;
    s_peer_data_interface_version_valid = false;
    if (s_realtime_data_stream_active) {
        bridge_notify_realtime_data_halted("tcp_client_closed");
        s_realtime_data_stream_active = false;
    }
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
    char keepalive_payload[BRIDGE_TEXT_PAYLOAD_MAX + 1U] = {0};
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

    if ((s_server_live_integer & 1U) != 0U) {
        s_server_live_integer++;
    }
    if ((s_client_live_integer & 1U) == 0U) {
        s_client_live_integer++;
    }

    bridge_build_keepalive_payload(
        keepalive_payload,
        sizeof(keepalive_payload),
        "ka_req",
        s_active_session_id,
        request_id);
    bridge_send_frame(BRIDGE_TRANSPORT_TCP, BRIDGE_MESSAGE_KEEPALIVE, &synthetic_keepalive, keepalive_payload);
    bridge_send_unsolicited_usb_frame(BRIDGE_MESSAGE_KEEPALIVE, keepalive_payload);
    s_last_keepalive_tx_us = esp_timer_get_time();
    s_keepalive_response_pending = true;
    s_keepalive_empty_window_count = 0U;
    bridge_start_keepalive_window(false);
    bridge_enter_state(BRIDGE_STATE_KEEPALIVE_CLIENT_RETURN);
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
 * @brief Handle keepalive timeout exhaustion and restart the link workflow.
 *
 * @details The bridge retries keepalive response timeouts three times in
 * `KeepAliveClientReturn`. If all retries fail, the TCP session is closed and
 * the state machine returns to initialize wait.
 *
 * @param[in] fault_reason Short ASCII reason text.
 */
static void bridge_handle_running_integer_failure(const char *fault_reason)
{
    const char *reason_text = (fault_reason != NULL) ? fault_reason : "running_integer_failure";

    bridge_record_error(reason_text);
    ESP_LOGE(TAG,
             "Running-integer timeout retries exhausted (%" PRIu32 "/%" PRIu32 "): %s",
             s_running_integer_retry_count,
             BRIDGE_RUNNING_INTEGER_RETRY_LIMIT,
             reason_text);
    bridge_notify_usb_fault("keepalive_retry_exhausted");
    bridge_close_tcp_client();
    bridge_enter_state(BRIDGE_STATE_INITIALIZE);
    s_server_live_integer = 0U;
    s_client_live_integer = BRIDGE_DEVICE_LIVE_START;
    s_tcp_sequence = 0U;
    s_running_integer_retry_count = 0U;
}

/**
 * @brief Service the server-send phase of keepalive supervision.
 *
 * @details In `KeepAliveServerSend`, the bridge emits one keepalive request
 * using the current even `ServerLiveInteger`, then transitions into
 * `KeepAliveClientReturn` to wait for a valid client response.
 */
static void bridge_service_keepalive_engine(void)
{
    int64_t now_us = 0;

    if (s_tcp_client_fd < 0 ||
        s_keepalive_response_pending ||
        s_bridge_state != BRIDGE_STATE_KEEPALIVE_SERVER_SEND) {
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
 * @brief Enforce keepalive response timeouts while waiting for the client.
 *
 * @details Timeout retries are the only TopLayer retry cause in the keepalive
 * phases. Counter mismatches and stale metadata are logged and ignored while
 * the bridge keeps waiting in `KeepAliveClientReturn` until timeout.
 */
static void bridge_service_transport_watchdog(void)
{
    if (s_tcp_client_fd < 0 ||
        !s_keepalive_response_pending ||
        s_last_keepalive_tx_us <= 0 ||
        s_bridge_state != BRIDGE_STATE_KEEPALIVE_CLIENT_RETURN) {
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
    s_running_integer_retry_count++;
    bridge_record_error("keepalive_response_timeout");
    ESP_LOGW(TAG,
             "Keepalive response timeout retry (%" PRIu32 "/%" PRIu32 "): sid=%" PRIu32
             " req=%" PRIu32 " after %u ms",
             s_running_integer_retry_count,
             BRIDGE_RUNNING_INTEGER_RETRY_LIMIT,
             s_active_session_id,
             s_pending_keepalive_request_id,
             BRIDGE_KEEPALIVE_WAIT_WINDOW_MS);

    if (s_running_integer_retry_count >= BRIDGE_RUNNING_INTEGER_RETRY_LIMIT) {
        bridge_handle_running_integer_failure("keepalive_response_timeout");
        return;
    }

    bridge_enter_state(BRIDGE_STATE_KEEPALIVE_SERVER_SEND);
    bridge_send_server_keepalive(true);
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
    char payload_text[BRIDGE_TEXT_PAYLOAD_MAX + 1U] = {0};

    if (frame == NULL) {
        return;
    }

    if (frame->payload_length > 0U) {
        size_t text_copy_len = (frame->payload_length < BRIDGE_TEXT_PAYLOAD_MAX)
                               ? frame->payload_length : BRIDGE_TEXT_PAYLOAD_MAX;
        memcpy(payload_text, frame->payload, text_copy_len);
        payload_text[text_copy_len] = '\0';
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
        s_realtime_data_sequence = 0U;
        s_realtime_data_stream_active = false;
        s_peer_data_interface_version = 0U;
        s_peer_data_interface_version_valid = false;
        bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "reset_ack");
        bridge_close_tcp_client();
        break;
    case BRIDGE_MESSAGE_INITIALIZE:
    {
        uint32_t peer_data_version = 0U;
        bool has_peer_data_version = bridge_try_parse_u32_payload_value(
            payload_text,
            "data_ver",
            &peer_data_version);
        bridge_enter_state(BRIDGE_STATE_INITIALIZE);
        s_running_integer_retry_count = 0U;
        s_keepalive_window_has_message = false;
        s_keepalive_window_started_us = 0;
        s_keepalive_empty_window_count = 0U;
        s_active_session_id = 0U;
        s_pending_keepalive_request_id = 0U;
        s_last_completed_keepalive_request_id = 0U;
        s_next_keepalive_request_id = 1U;
        s_realtime_data_sequence = 0U;
        s_realtime_data_stream_active = false;
        if (!has_peer_data_version ||
            peer_data_version != BRIDGE_REALTIME_DATA_INTERFACE_VERSION) {
            bridge_record_error("data_interface_version_mismatch");
            bridge_enter_state(BRIDGE_STATE_ERROR);
            bridge_send_frame(transport, BRIDGE_MESSAGE_ERROR, frame, "data_interface_version_mismatch");
            bridge_notify_usb_fault("critical_error_data_interface_version_mismatch");
            break;
        }
        s_peer_data_interface_version = peer_data_version;
        s_peer_data_interface_version_valid = true;
        bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "initialize_ack");
        break;
    }
    case BRIDGE_MESSAGE_CONNECT:
    {
        char connect_payload[BRIDGE_TEXT_PAYLOAD_MAX + 1U] = {0};
        bridge_enter_state(BRIDGE_STATE_CONNECT);
        s_server_live_integer = 0U;
        s_client_live_integer = BRIDGE_DEVICE_LIVE_START;
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
        if (!s_peer_data_interface_version_valid ||
            s_peer_data_interface_version != BRIDGE_REALTIME_DATA_INTERFACE_VERSION) {
            bridge_record_error("data_interface_version_mismatch");
            bridge_enter_state(BRIDGE_STATE_ERROR);
            bridge_send_frame(transport, BRIDGE_MESSAGE_ERROR, frame, "data_interface_version_mismatch");
            bridge_notify_usb_fault("critical_error_data_interface_version_mismatch");
            break;
        }
        snprintf(connect_payload,
                 sizeof(connect_payload),
                 "client_connected;sid=%" PRIu32,
                 s_active_session_id);
        if (transport == BRIDGE_TRANSPORT_TCP) {
            bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, connect_payload);
            bridge_send_unsolicited_usb_frame(BRIDGE_MESSAGE_ACK, connect_payload);
            bridge_enter_state(BRIDGE_STATE_KEEPALIVE_SERVER_SEND);
        } else {
            bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, connect_payload);
        }
        if (transport == BRIDGE_TRANSPORT_USB && s_tcp_client_fd >= 0) {
            bridge_send_unsolicited_usb_frame(BRIDGE_MESSAGE_ACK, connect_payload);
            bridge_enter_state(BRIDGE_STATE_KEEPALIVE_SERVER_SEND);
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
            uint32_t payload_data_version = 0U;
            bool has_session_id = bridge_try_parse_u32_payload_value(payload_text, "sid", &payload_session_id);
            bool has_request_id = bridge_try_parse_u32_payload_value(payload_text, "req", &payload_request_id);
            bool has_data_version = bridge_try_parse_u32_payload_value(payload_text, "rver", &payload_data_version);
            uint32_t expected_client_live_integer = s_server_live_integer + 1U;
            uint32_t measured_delay_ms = 0U;

            if (!has_session_id || !has_request_id) {
                bridge_record_error("keepalive_missing_sid_req");
                ESP_LOGW(TAG, "Ignoring keepalive response missing sid/req metadata");
                break;
            }
            if (payload_session_id != s_active_session_id) {
                bridge_record_error("keepalive_stale_session");
                ESP_LOGW(TAG,
                         "Ignoring stale keepalive session response sid=%" PRIu32 " active=%" PRIu32,
                         payload_session_id,
                         s_active_session_id);
                break;
            }

            if (!s_keepalive_response_pending || s_pending_keepalive_request_id == 0U) {
                if (payload_request_id == s_last_completed_keepalive_request_id &&
                    s_last_completed_keepalive_request_id != 0U) {
                    ESP_LOGI(TAG,
                             "Ignoring duplicate keepalive response already completed: sid=%" PRIu32 " req=%" PRIu32,
                             payload_session_id,
                             payload_request_id);
                } else {
                    bridge_record_error("keepalive_unexpected_no_pending");
                    ESP_LOGW(TAG,
                             "Ignoring unexpected keepalive response with no pending request (sid=%" PRIu32 ", req=%" PRIu32 ")",
                             payload_session_id,
                             payload_request_id);
                }
                break;
            }
            if (s_bridge_state != BRIDGE_STATE_KEEPALIVE_CLIENT_RETURN) {
                bridge_record_error("keepalive_unexpected_state");
                ESP_LOGW(TAG, "Ignoring keepalive response outside KeepAliveClientReturn");
                break;
            }
            if (!has_data_version ||
                payload_data_version != BRIDGE_REALTIME_DATA_INTERFACE_VERSION) {
                bridge_record_error("data_interface_version_mismatch");
                bridge_enter_state(BRIDGE_STATE_ERROR);
                bridge_send_frame(transport, BRIDGE_MESSAGE_ERROR, frame, "data_interface_version_mismatch");
                bridge_notify_usb_fault("critical_error_data_interface_version_mismatch");
                break;
            }
            if (payload_request_id != s_pending_keepalive_request_id) {
                if (payload_request_id == s_last_completed_keepalive_request_id &&
                    s_last_completed_keepalive_request_id != 0U) {
                    ESP_LOGI(TAG,
                             "Ignoring duplicate keepalive response already completed: sid=%" PRIu32 " req=%" PRIu32,
                             payload_session_id,
                             payload_request_id);
                } else {
                    bridge_record_error("keepalive_stale_request_id");
                    ESP_LOGW(TAG,
                             "Ignoring stale keepalive request response req=%" PRIu32 " pending=%" PRIu32,
                             payload_request_id,
                             s_pending_keepalive_request_id);
                }
                break;
            }
            if (frame->host_live_integer != s_server_live_integer ||
                frame->device_live_integer != expected_client_live_integer ||
                ((frame->host_live_integer & 1U) != 0U) ||
                ((frame->device_live_integer & 1U) == 0U)) {
                bridge_record_error("keepalive_counter_mismatch");
                ESP_LOGW(TAG,
                         "Ignoring keepalive counter mismatch while waiting: expected server=%" PRIu32
                         " client=%" PRIu32 ", got server=%" PRIu32 " client=%" PRIu32,
                         s_server_live_integer,
                         expected_client_live_integer,
                         frame->host_live_integer,
                         frame->device_live_integer);
                break;
            }

            measured_delay_ms = bridge_compute_pending_keepalive_delay_ms();
            s_transport_last_delay_ms = (int32_t)measured_delay_ms;
            if (measured_delay_ms > s_transport_max_delay_ms) {
                s_transport_max_delay_ms = measured_delay_ms;
            }
            s_last_tcp_activity_us = esp_timer_get_time();
            s_client_live_integer = frame->device_live_integer;
            s_server_live_integer = s_client_live_integer + 1U;
            if ((s_server_live_integer & 1U) != 0U) {
                s_server_live_integer++;
            }
            bridge_mark_keepalive_window_message();
            s_keepalive_response_pending = false;
            s_keepalive_window_started_us = 0;
            s_pending_keepalive_request_id = 0U;
            s_last_completed_keepalive_request_id = payload_request_id;
            s_running_integer_retry_count = 0U;
            bridge_enter_state(BRIDGE_STATE_KEEPALIVE_SERVER_SEND);
            char keepalive_status_payload[BRIDGE_TEXT_PAYLOAD_MAX + 1U] = {0};
            bridge_build_keepalive_payload(
                keepalive_status_payload,
                sizeof(keepalive_status_payload),
                "ka_resp",
                s_active_session_id,
                payload_request_id);
            bridge_send_unsolicited_usb_frame(BRIDGE_MESSAGE_KEEPALIVE, keepalive_status_payload);
            bridge_send_realtime_data_packet();
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
                bridge_record_error("wifi_enable_failed");
                bridge_send_frame(transport, BRIDGE_MESSAGE_ERROR, frame, "wifi_enable_failed");
            }
        } else if (transport == BRIDGE_TRANSPORT_USB && strcmp(payload_text, "telemetry_reset") == 0) {
            bridge_reset_telemetry_counters();
            bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "telemetry_reset_ack");
            bridge_send_telemetry_update_usb();
        } else if (transport == BRIDGE_TRANSPORT_USB && strcmp(payload_text, "telemetry_reset_max_delay") == 0) {
            bridge_reset_transport_max_delay();
            bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "telemetry_reset_max_delay_ack");
            bridge_send_telemetry_update_usb();
        } else if (transport == BRIDGE_TRANSPORT_USB
                   && frame->payload_length > 0U
                   && frame->payload[0] != BRIDGE_DATA_MAGIC_DOWNLINK) {
            /* Text DATA event from simulator UI/runtime: forward to TCP client. */
            if (!bridge_is_keepalive_session_active()) {
                bridge_record_error("data_ignored_not_in_keepalive_session");
                bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "data_ignored_not_in_keepalive_session");
            } else if (s_tcp_client_fd >= 0) {
                bridge_send_frame_binary(BRIDGE_TRANSPORT_TCP, BRIDGE_MESSAGE_DATA, frame,
                                         frame->payload, frame->payload_length);
                bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "data_forwarded_tcp");
            } else {
                bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "data_tcp_not_connected");
            }
        } else if (transport == BRIDGE_TRANSPORT_USB
                   && frame->payload_length > 0U
                   && frame->payload[0] == BRIDGE_DATA_MAGIC_DOWNLINK) {
            /* Binary downlink packet (server → client): forward transparently to TCP. */
            if (!bridge_is_keepalive_session_active() || s_tcp_client_fd < 0) {
                bridge_record_error("data_ignored_not_in_keepalive_session");
            } else {
                bridge_send_frame_binary(BRIDGE_TRANSPORT_TCP, BRIDGE_MESSAGE_DATA, frame,
                                         frame->payload, frame->payload_length);
            }
        } else if (transport == BRIDGE_TRANSPORT_TCP
                   && frame->payload_length > 0U
                   && frame->payload[0] == BRIDGE_DATA_MAGIC_UPLINK) {
            /* Binary uplink packet (client → server): forward transparently to USB. */
            if (!bridge_is_keepalive_session_active()) {
                bridge_record_error("data_ignored_not_in_keepalive_session");
            } else {
                bridge_send_frame_binary(BRIDGE_TRANSPORT_USB, BRIDGE_MESSAGE_DATA, frame,
                                         frame->payload, frame->payload_length);
            }
        } else if (transport == BRIDGE_TRANSPORT_TCP
                   && frame->payload_length > 0U) {
            /* Text DATA event from client (e.g. DataSimulationOn/OFF): forward to USB host runtime. */
            if (!bridge_is_keepalive_session_active()) {
                bridge_record_error("data_ignored_not_in_keepalive_session");
                bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "data_ignored_not_in_keepalive_session");
            } else {
                bridge_send_frame_binary(BRIDGE_TRANSPORT_USB, BRIDGE_MESSAGE_DATA, frame,
                                         frame->payload, frame->payload_length);
                bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "data_forwarded_usb");
            }
        } else {
            bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "data_ack");
        }
        break;
    case BRIDGE_MESSAGE_ERROR:
        bridge_record_error("bridge_message_error");
        bridge_enter_state(BRIDGE_STATE_ERROR);
        bridge_send_frame(transport, BRIDGE_MESSAGE_ACK, frame, "error_ack");
        break;
    case BRIDGE_MESSAGE_ACK:
    default:
        bridge_record_error("unsupported_message_type");
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

    /* Bound TCP send so a slow or absent client cannot block the USB service loop. */
    struct timeval snd_tv = { .tv_sec = 0, .tv_usec = 50000 }; /* 50 ms */
    (void)setsockopt(s_tcp_client_fd, SOL_SOCKET, SO_SNDTIMEO, &snd_tv, sizeof(snd_tv));

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
    uint8_t temp_buffer[BRIDGE_FRAME_MAX_SIZE] = {0};
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

    while (true) {
        int received = recv(s_tcp_client_fd, temp_buffer, sizeof(temp_buffer), 0);
        if (received > 0) {
            size_t copy_length = (size_t)received;
            if ((s_tcp_rx_length + copy_length) > sizeof(s_tcp_rx_buffer)) {
                bridge_record_error("tcp_rx_buffer_overflow");
                ESP_LOGW(TAG,
                         "Closing TCP client: RX buffer overflow (%u + %u > %u)",
                         (unsigned)s_tcp_rx_length,
                         (unsigned)copy_length,
                         (unsigned)sizeof(s_tcp_rx_buffer));
                bridge_close_tcp_client();
                return;
            }
            memcpy(&s_tcp_rx_buffer[s_tcp_rx_length], temp_buffer, copy_length);
            s_tcp_rx_length += copy_length;

            /* Non-blocking socket: a short read means kernel RX queue is drained. */
            if (copy_length < sizeof(temp_buffer)) {
                break;
            }
            continue;
        }

        if (received == 0) {
            bridge_close_tcp_client();
            return;
        }
        break;
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
            bridge_record_error("tcp_payload_too_large");
            ESP_LOGW(TAG, "Closing TCP client: payload too large (%u)", payload_length);
            bridge_close_tcp_client();
            return;
        }
        if (s_tcp_rx_length < frame_length) {
            return;
        }

        if (bridge_parse_frame(s_tcp_rx_buffer, frame_length, &frame) != ESP_OK) {
            ESP_LOGW(TAG, "Closing TCP client: frame parse failed");
            bridge_record_error("tcp_frame_parse_failed");
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
void communication_functions_run(void)
{
    /* Streaming accumulation buffer for USB serial frames.  Two max-size
     * frames fit so a large binary frame and a control frame can both be
     * buffered before the next parse pass. */
    static uint8_t usb_accum[BRIDGE_FRAME_MAX_SIZE * 2U];
    static size_t  usb_accum_len = 0U;

    esp_err_t init_result = bridge_transport_init();
    s_reset_cycle_started_us = esp_timer_get_time();

    if (init_result != ESP_OK) {
        bridge_record_error("transport_init_failed");
        bridge_enter_state(BRIDGE_STATE_ERROR);
        while (1) {
            vTaskDelay(pdMS_TO_TICKS(250));
        }
    }

    init_result = bridge_wifi_init_softap();
    if (init_result != ESP_OK) {
        bridge_record_error("wifi_init_failed");
        bridge_enter_state(BRIDGE_STATE_ERROR);
        while (1) {
            vTaskDelay(pdMS_TO_TICKS(250));
        }
    }

    init_result = bridge_tcp_server_init();
    if (init_result != ESP_OK) {
        bridge_record_error("tcp_server_init_failed");
        bridge_enter_state(BRIDGE_STATE_ERROR);
        while (1) {
            vTaskDelay(pdMS_TO_TICKS(250));
        }
    }

    while (1) {
        if (s_wifi_transport_enabled) {
            bridge_accept_tcp_client();
            bridge_poll_tcp_client();
            bridge_service_keepalive_engine();
            bridge_service_transport_watchdog();
        }

        /* Read new bytes into the accumulation buffer. */
        size_t space = sizeof(usb_accum) - usb_accum_len;
        if (space > 0U) {
            int received = usb_serial_jtag_read_bytes(
                usb_accum + usb_accum_len,
                space,
                pdMS_TO_TICKS(BRIDGE_POLL_DELAY_MS));
            if (received > 0) {
                usb_accum_len += (size_t)received;
            } else {
                vTaskDelay(pdMS_TO_TICKS(BRIDGE_POLL_DELAY_MS));
            }
        }

        /* Drain all complete frames from the accumulation buffer. */
        while (usb_accum_len >= BRIDGE_FRAME_OVERHEAD) {
            /* Find SOF marker. */
            size_t sof_pos = 0U;
            bool found = false;
            while (sof_pos + 1U < usb_accum_len) {
                if (usb_accum[sof_pos]      == BRIDGE_SOF_BYTE0 &&
                    usb_accum[sof_pos + 1U] == BRIDGE_SOF_BYTE1) {
                    found = true;
                    break;
                }
                sof_pos++;
            }
            if (!found) {
                /* Keep the last byte in case it is the first SOF byte. */
                usb_accum[0] = usb_accum[usb_accum_len - 1U];
                usb_accum_len = 1U;
                break;
            }
            if (sof_pos > 0U) {
                usb_accum_len -= sof_pos;
                memmove(usb_accum, usb_accum + sof_pos, usb_accum_len);
            }
            if (usb_accum_len < BRIDGE_FRAME_OVERHEAD) {
                break; /* Need more bytes. */
            }

            uint16_t payload_len = (uint16_t)(usb_accum[3] | ((uint16_t)usb_accum[4] << 8));
            size_t   frame_len   = BRIDGE_FRAME_OVERHEAD + (size_t)payload_len;

            if (payload_len > BRIDGE_FRAME_MAX_PAYLOAD) {
                /* Oversized frame: skip past the SOF and resync. */
                usb_accum_len--;
                memmove(usb_accum, usb_accum + 1U, usb_accum_len);
                continue;
            }
            if (usb_accum_len < frame_len) {
                break; /* Incomplete frame: wait for more bytes. */
            }

            bridge_frame_t frame = {0};
            esp_err_t parse_result = bridge_parse_frame(usb_accum, frame_len, &frame);

            /* Consume the frame bytes regardless of parse outcome. */
            usb_accum_len -= frame_len;
            memmove(usb_accum, usb_accum + frame_len, usb_accum_len);

            if (parse_result == ESP_OK) {
                bridge_handle_frame(BRIDGE_TRANSPORT_USB, &frame);
            }
        }
    }
}
