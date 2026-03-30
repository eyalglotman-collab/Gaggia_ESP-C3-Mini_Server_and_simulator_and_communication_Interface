/*
 * SPDX-FileCopyrightText: 2026 Eyal Espresso
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Generic LCD <-> Controller Protocol (GLCP) wire framing constants.
 */
#define GLCP_WIRE_SOF              (0xA55AU)
#define GLCP_PROTOCOL_MAJOR        (1U)
#define GLCP_PROTOCOL_MINOR        (0U)
#define GLCP_HEADER_SIZE_BYTES     (22U)
#define GLCP_MAX_PAYLOAD_BYTES     (2300U)

/**
 * @brief Negotiation/data-plane feature capability bits.
 */
#define GLCP_CAP_BINARY_STATE_DATA    (1ULL << 0)
#define GLCP_CAP_SCHEMA_CHUNKS        (1ULL << 1)
#define GLCP_CAP_COMPRESSED_PAYLOAD   (1ULL << 2)
#define GLCP_CAP_ACK_ROUTING          (1ULL << 3)

/**
 * @brief Frame flags carried by each packet.
 */
typedef enum {
    GLCP_FLAG_ACK_REQUIRED = (1U << 0),
    GLCP_FLAG_IS_RESPONSE = (1U << 1),
    GLCP_FLAG_IS_ERROR = (1U << 2),
} glcp_frame_flag_t;

/**
 * @brief Message IDs for control-plane and data-plane exchanges.
 */
typedef enum {
    GLCP_MSG_HELLO = 0x0001,
    GLCP_MSG_HELLO_ACK = 0x0002,
    GLCP_MSG_NACK = 0x0003,

    GLCP_MSG_SCHEMA_GET = 0x0010,
    GLCP_MSG_SCHEMA_CHUNK = 0x0011,

    GLCP_MSG_PING = 0x0020,
    GLCP_MSG_PONG = 0x0021,

    GLCP_MSG_SYSTEM_STATE = 0x0100,
    GLCP_MSG_SENSOR_SNAPSHOT = 0x0101,
    GLCP_MSG_SHOT_SNAPSHOT = 0x0102,

    GLCP_MSG_PROFILE_GET = 0x0200,
    GLCP_MSG_PROFILE_SET = 0x0201,
    GLCP_MSG_PROFILE_DATA = 0x0202,

    GLCP_MSG_SETTINGS_GET = 0x0210,
    GLCP_MSG_SETTINGS_SET = 0x0211,
    GLCP_MSG_SETTINGS_DATA = 0x0212,

    GLCP_MSG_EVENT_RAISED = 0x0300,
    GLCP_MSG_COMMAND_APPLY = 0x0301,
} glcp_msg_id_t;

/**
 * @brief Parser/build status values for frame and payload helpers.
 */
typedef enum {
    GLCP_STATUS_OK = 0,
    GLCP_STATUS_INVALID_ARG,
    GLCP_STATUS_BUFFER_TOO_SMALL,
    GLCP_STATUS_INVALID_SOF,
    GLCP_STATUS_UNSUPPORTED_VERSION,
    GLCP_STATUS_INVALID_LENGTH,
    GLCP_STATUS_PAYLOAD_TOO_LARGE,
    GLCP_STATUS_PAYLOAD_CRC_MISMATCH,
    GLCP_STATUS_SCHEMA_NOT_NEGOTIATED,
    GLCP_STATUS_NO_COMMON_SCHEMA,
} glcp_status_t;

/**
 * @brief Fixed wire header. Always serialize field-by-field.
 */
typedef struct {
    uint16_t sof;
    uint8_t major;
    uint8_t minor;
    uint16_t msg_id;
    uint16_t flags;
    uint32_t session_id;
    uint32_t seq;
    uint16_t payload_len;
    uint32_t payload_crc32;
} glcp_frame_header_t;

/**
 * @brief Decoded frame view into an existing byte buffer.
 */
typedef struct {
    glcp_frame_header_t header;
    const uint8_t *payload;
} glcp_frame_view_t;

/**
 * @brief Client hello payload (schema hashes follow the fixed part).
 */
typedef struct {
    uint16_t max_frame_size;
    uint16_t heartbeat_ms;
    uint32_t client_build_id;
    uint64_t capability_bits;
    uint8_t supported_major_min;
    uint8_t supported_major_max;
    uint16_t schema_hash_count;
} glcp_hello_t;

/**
 * @brief Server hello-ack payload.
 */
typedef struct {
    uint8_t accepted;
    uint8_t selected_major;
    uint8_t selected_minor;
    uint16_t server_max_frame_size;
    uint16_t heartbeat_ms;
    uint32_t server_build_id;
    uint64_t capability_bits;
    uint64_t selected_schema_hash;
} glcp_hello_ack_t;

/**
 * @brief Optional schema descriptor chunk payload.
 */
typedef struct {
    uint64_t schema_hash;
    uint16_t chunk_index;
    uint16_t chunk_count;
    uint16_t chunk_len;
    const uint8_t *chunk_data;
} glcp_schema_chunk_t;

/**
 * @brief Default frame header initializer for a given message and session/seq.
 */
void glcp_make_default_header(glcp_frame_header_t *header,
                              glcp_msg_id_t msg_id,
                              uint16_t flags,
                              uint32_t session_id,
                              uint32_t seq);

/**
 * @brief Compute CRC32 (IEEE 802.3 reflected polynomial).
 */
uint32_t glcp_crc32(const uint8_t *data, size_t data_len);

/**
 * @brief Serialize one frame (`header + payload`) into `out_buffer`.
 */
glcp_status_t glcp_build_frame(const glcp_frame_header_t *header,
                               const uint8_t *payload,
                               size_t payload_len,
                               uint8_t *out_buffer,
                               size_t out_buffer_size,
                               size_t *out_frame_len);

/**
 * @brief Parse one frame and validate payload CRC.
 */
glcp_status_t glcp_parse_frame(const uint8_t *frame,
                               size_t frame_len,
                               glcp_frame_view_t *out_view);

/**
 * @brief Return number of bytes required for serialized hello payload.
 */
size_t glcp_hello_encoded_size(uint16_t schema_hash_count);

/**
 * @brief Encode hello payload (fixed section + hash list).
 */
glcp_status_t glcp_encode_hello(const glcp_hello_t *hello,
                                const uint64_t *schema_hashes,
                                size_t schema_hash_count,
                                uint8_t *out_payload,
                                size_t out_payload_size,
                                size_t *out_payload_len);

/**
 * @brief Decode hello payload and copy schema hashes into caller-provided array.
 */
glcp_status_t glcp_decode_hello(const uint8_t *payload,
                                size_t payload_len,
                                glcp_hello_t *out_hello,
                                uint64_t *out_schema_hashes,
                                size_t out_schema_hashes_capacity,
                                size_t *out_schema_hash_count);

/**
 * @brief Encode/decode fixed hello-ack payload.
 */
glcp_status_t glcp_encode_hello_ack(const glcp_hello_ack_t *ack,
                                    uint8_t *out_payload,
                                    size_t out_payload_size,
                                    size_t *out_payload_len);

glcp_status_t glcp_decode_hello_ack(const uint8_t *payload,
                                    size_t payload_len,
                                    glcp_hello_ack_t *out_ack);

/**
 * @brief Find first common schema hash preserving server preference order.
 */
glcp_status_t glcp_select_schema_hash(const uint64_t *server_schema_hashes,
                                      size_t server_schema_count,
                                      const uint64_t *peer_schema_hashes,
                                      size_t peer_schema_count,
                                      uint64_t *out_selected_hash);

#ifdef __cplusplus
}
#endif
