/*
 * SPDX-FileCopyrightText: 2026 Eyal Espresso
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include "ProtocolLCD_Controller.h"

#include <string.h>

#define GLCP_HELLO_FIXED_SIZE_BYTES      (20U)
#define GLCP_HELLO_ACK_SIZE_BYTES        (28U)

static void glcp_write_u16_le(uint8_t *dst, uint16_t value)
{
    dst[0] = (uint8_t)(value & 0xFFU);
    dst[1] = (uint8_t)((value >> 8) & 0xFFU);
}

static void glcp_write_u32_le(uint8_t *dst, uint32_t value)
{
    dst[0] = (uint8_t)(value & 0xFFU);
    dst[1] = (uint8_t)((value >> 8) & 0xFFU);
    dst[2] = (uint8_t)((value >> 16) & 0xFFU);
    dst[3] = (uint8_t)((value >> 24) & 0xFFU);
}

static void glcp_write_u64_le(uint8_t *dst, uint64_t value)
{
    for (uint8_t idx = 0U; idx < 8U; ++idx) {
        dst[idx] = (uint8_t)((value >> (8U * idx)) & 0xFFU);
    }
}

static uint16_t glcp_read_u16_le(const uint8_t *src)
{
    return (uint16_t)src[0] | ((uint16_t)src[1] << 8);
}

static uint32_t glcp_read_u32_le(const uint8_t *src)
{
    return (uint32_t)src[0]
           | ((uint32_t)src[1] << 8)
           | ((uint32_t)src[2] << 16)
           | ((uint32_t)src[3] << 24);
}

static uint64_t glcp_read_u64_le(const uint8_t *src)
{
    uint64_t value = 0U;

    for (uint8_t idx = 0U; idx < 8U; ++idx) {
        value |= ((uint64_t)src[idx]) << (8U * idx);
    }

    return value;
}

void glcp_make_default_header(glcp_frame_header_t *header,
                              glcp_msg_id_t msg_id,
                              uint16_t flags,
                              uint32_t session_id,
                              uint32_t seq)
{
    if (header == NULL) {
        return;
    }

    header->sof = GLCP_WIRE_SOF;
    header->major = GLCP_PROTOCOL_MAJOR;
    header->minor = GLCP_PROTOCOL_MINOR;
    header->msg_id = (uint16_t)msg_id;
    header->flags = flags;
    header->session_id = session_id;
    header->seq = seq;
    header->payload_len = 0U;
    header->payload_crc32 = 0U;
}

uint32_t glcp_crc32(const uint8_t *data, size_t data_len)
{
    uint32_t crc = 0xFFFFFFFFU;

    if (data == NULL && data_len != 0U) {
        return 0U;
    }

    for (size_t i = 0U; i < data_len; ++i) {
        crc ^= (uint32_t)data[i];
        for (uint8_t bit = 0U; bit < 8U; ++bit) {
            if ((crc & 1U) != 0U) {
                crc = (crc >> 1U) ^ 0xEDB88320U;
            } else {
                crc >>= 1U;
            }
        }
    }

    return ~crc;
}

glcp_status_t glcp_build_frame(const glcp_frame_header_t *header,
                               const uint8_t *payload,
                               size_t payload_len,
                               uint8_t *out_buffer,
                               size_t out_buffer_size,
                               size_t *out_frame_len)
{
    glcp_frame_header_t runtime_header = {0};
    size_t frame_len = GLCP_HEADER_SIZE_BYTES + payload_len;

    if (header == NULL || out_buffer == NULL || out_frame_len == NULL) {
        return GLCP_STATUS_INVALID_ARG;
    }

    if (payload_len > GLCP_MAX_PAYLOAD_BYTES || payload_len > UINT16_MAX) {
        return GLCP_STATUS_PAYLOAD_TOO_LARGE;
    }

    if (payload_len > 0U && payload == NULL) {
        return GLCP_STATUS_INVALID_ARG;
    }

    if (out_buffer_size < frame_len) {
        return GLCP_STATUS_BUFFER_TOO_SMALL;
    }

    runtime_header = *header;
    runtime_header.sof = GLCP_WIRE_SOF;
    runtime_header.major = GLCP_PROTOCOL_MAJOR;
    runtime_header.minor = GLCP_PROTOCOL_MINOR;
    runtime_header.payload_len = (uint16_t)payload_len;
    runtime_header.payload_crc32 = (payload_len > 0U) ? glcp_crc32(payload, payload_len) : 0U;

    glcp_write_u16_le(&out_buffer[0], runtime_header.sof);
    out_buffer[2] = runtime_header.major;
    out_buffer[3] = runtime_header.minor;
    glcp_write_u16_le(&out_buffer[4], runtime_header.msg_id);
    glcp_write_u16_le(&out_buffer[6], runtime_header.flags);
    glcp_write_u32_le(&out_buffer[8], runtime_header.session_id);
    glcp_write_u32_le(&out_buffer[12], runtime_header.seq);
    glcp_write_u16_le(&out_buffer[16], runtime_header.payload_len);
    glcp_write_u32_le(&out_buffer[18], runtime_header.payload_crc32);

    if (payload_len > 0U) {
        memcpy(&out_buffer[GLCP_HEADER_SIZE_BYTES], payload, payload_len);
    }

    *out_frame_len = frame_len;
    return GLCP_STATUS_OK;
}

glcp_status_t glcp_parse_frame(const uint8_t *frame,
                               size_t frame_len,
                               glcp_frame_view_t *out_view)
{
    glcp_frame_header_t parsed = {0};
    size_t expected_len = 0U;
    uint32_t computed_crc = 0U;
    const uint8_t *payload_ptr = NULL;

    if (frame == NULL || out_view == NULL) {
        return GLCP_STATUS_INVALID_ARG;
    }

    if (frame_len < GLCP_HEADER_SIZE_BYTES) {
        return GLCP_STATUS_INVALID_LENGTH;
    }

    parsed.sof = glcp_read_u16_le(&frame[0]);
    parsed.major = frame[2];
    parsed.minor = frame[3];
    parsed.msg_id = glcp_read_u16_le(&frame[4]);
    parsed.flags = glcp_read_u16_le(&frame[6]);
    parsed.session_id = glcp_read_u32_le(&frame[8]);
    parsed.seq = glcp_read_u32_le(&frame[12]);
    parsed.payload_len = glcp_read_u16_le(&frame[16]);
    parsed.payload_crc32 = glcp_read_u32_le(&frame[18]);

    if (parsed.sof != GLCP_WIRE_SOF) {
        return GLCP_STATUS_INVALID_SOF;
    }

    if (parsed.major != GLCP_PROTOCOL_MAJOR) {
        return GLCP_STATUS_UNSUPPORTED_VERSION;
    }

    if (parsed.payload_len > GLCP_MAX_PAYLOAD_BYTES) {
        return GLCP_STATUS_PAYLOAD_TOO_LARGE;
    }

    expected_len = GLCP_HEADER_SIZE_BYTES + parsed.payload_len;
    if (frame_len != expected_len) {
        return GLCP_STATUS_INVALID_LENGTH;
    }

    payload_ptr = &frame[GLCP_HEADER_SIZE_BYTES];
    if (parsed.payload_len > 0U) {
        computed_crc = glcp_crc32(payload_ptr, parsed.payload_len);
        if (computed_crc != parsed.payload_crc32) {
            return GLCP_STATUS_PAYLOAD_CRC_MISMATCH;
        }
    } else if (parsed.payload_crc32 != 0U) {
        return GLCP_STATUS_PAYLOAD_CRC_MISMATCH;
    }

    out_view->header = parsed;
    out_view->payload = payload_ptr;
    return GLCP_STATUS_OK;
}

size_t glcp_hello_encoded_size(uint16_t schema_hash_count)
{
    return GLCP_HELLO_FIXED_SIZE_BYTES + ((size_t)schema_hash_count * sizeof(uint64_t));
}

glcp_status_t glcp_encode_hello(const glcp_hello_t *hello,
                                const uint64_t *schema_hashes,
                                size_t schema_hash_count,
                                uint8_t *out_payload,
                                size_t out_payload_size,
                                size_t *out_payload_len)
{
    size_t encoded_size = 0U;

    if (hello == NULL || out_payload == NULL || out_payload_len == NULL) {
        return GLCP_STATUS_INVALID_ARG;
    }

    if (schema_hash_count > UINT16_MAX) {
        return GLCP_STATUS_INVALID_ARG;
    }

    if (schema_hash_count > 0U && schema_hashes == NULL) {
        return GLCP_STATUS_INVALID_ARG;
    }

    encoded_size = glcp_hello_encoded_size((uint16_t)schema_hash_count);
    if (out_payload_size < encoded_size) {
        return GLCP_STATUS_BUFFER_TOO_SMALL;
    }

    glcp_write_u16_le(&out_payload[0], hello->max_frame_size);
    glcp_write_u16_le(&out_payload[2], hello->heartbeat_ms);
    glcp_write_u32_le(&out_payload[4], hello->client_build_id);
    glcp_write_u64_le(&out_payload[8], hello->capability_bits);
    out_payload[16] = hello->supported_major_min;
    out_payload[17] = hello->supported_major_max;
    glcp_write_u16_le(&out_payload[18], (uint16_t)schema_hash_count);

    for (size_t idx = 0U; idx < schema_hash_count; ++idx) {
        glcp_write_u64_le(&out_payload[GLCP_HELLO_FIXED_SIZE_BYTES + (idx * sizeof(uint64_t))],
                          schema_hashes[idx]);
    }

    *out_payload_len = encoded_size;
    return GLCP_STATUS_OK;
}

glcp_status_t glcp_decode_hello(const uint8_t *payload,
                                size_t payload_len,
                                glcp_hello_t *out_hello,
                                uint64_t *out_schema_hashes,
                                size_t out_schema_hashes_capacity,
                                size_t *out_schema_hash_count)
{
    uint16_t schema_hash_count = 0U;
    size_t expected_size = 0U;

    if (payload == NULL || out_hello == NULL || out_schema_hash_count == NULL) {
        return GLCP_STATUS_INVALID_ARG;
    }

    if (payload_len < GLCP_HELLO_FIXED_SIZE_BYTES) {
        return GLCP_STATUS_INVALID_LENGTH;
    }

    out_hello->max_frame_size = glcp_read_u16_le(&payload[0]);
    out_hello->heartbeat_ms = glcp_read_u16_le(&payload[2]);
    out_hello->client_build_id = glcp_read_u32_le(&payload[4]);
    out_hello->capability_bits = glcp_read_u64_le(&payload[8]);
    out_hello->supported_major_min = payload[16];
    out_hello->supported_major_max = payload[17];
    schema_hash_count = glcp_read_u16_le(&payload[18]);
    out_hello->schema_hash_count = schema_hash_count;

    expected_size = glcp_hello_encoded_size(schema_hash_count);
    if (payload_len != expected_size) {
        return GLCP_STATUS_INVALID_LENGTH;
    }

    if (schema_hash_count > 0U) {
        if (out_schema_hashes == NULL || out_schema_hashes_capacity < schema_hash_count) {
            return GLCP_STATUS_BUFFER_TOO_SMALL;
        }

        for (size_t idx = 0U; idx < schema_hash_count; ++idx) {
            out_schema_hashes[idx] =
                glcp_read_u64_le(&payload[GLCP_HELLO_FIXED_SIZE_BYTES + (idx * sizeof(uint64_t))]);
        }
    }

    *out_schema_hash_count = schema_hash_count;
    return GLCP_STATUS_OK;
}

glcp_status_t glcp_encode_hello_ack(const glcp_hello_ack_t *ack,
                                    uint8_t *out_payload,
                                    size_t out_payload_size,
                                    size_t *out_payload_len)
{
    if (ack == NULL || out_payload == NULL || out_payload_len == NULL) {
        return GLCP_STATUS_INVALID_ARG;
    }

    if (out_payload_size < GLCP_HELLO_ACK_SIZE_BYTES) {
        return GLCP_STATUS_BUFFER_TOO_SMALL;
    }

    out_payload[0] = ack->accepted;
    out_payload[1] = ack->selected_major;
    out_payload[2] = ack->selected_minor;
    out_payload[3] = 0U;
    glcp_write_u16_le(&out_payload[4], ack->server_max_frame_size);
    glcp_write_u16_le(&out_payload[6], ack->heartbeat_ms);
    glcp_write_u32_le(&out_payload[8], ack->server_build_id);
    glcp_write_u64_le(&out_payload[12], ack->capability_bits);
    glcp_write_u64_le(&out_payload[20], ack->selected_schema_hash);

    *out_payload_len = GLCP_HELLO_ACK_SIZE_BYTES;
    return GLCP_STATUS_OK;
}

glcp_status_t glcp_decode_hello_ack(const uint8_t *payload,
                                    size_t payload_len,
                                    glcp_hello_ack_t *out_ack)
{
    if (payload == NULL || out_ack == NULL) {
        return GLCP_STATUS_INVALID_ARG;
    }

    if (payload_len != GLCP_HELLO_ACK_SIZE_BYTES) {
        return GLCP_STATUS_INVALID_LENGTH;
    }

    out_ack->accepted = payload[0];
    out_ack->selected_major = payload[1];
    out_ack->selected_minor = payload[2];
    out_ack->server_max_frame_size = glcp_read_u16_le(&payload[4]);
    out_ack->heartbeat_ms = glcp_read_u16_le(&payload[6]);
    out_ack->server_build_id = glcp_read_u32_le(&payload[8]);
    out_ack->capability_bits = glcp_read_u64_le(&payload[12]);
    out_ack->selected_schema_hash = glcp_read_u64_le(&payload[20]);

    return GLCP_STATUS_OK;
}

glcp_status_t glcp_select_schema_hash(const uint64_t *server_schema_hashes,
                                      size_t server_schema_count,
                                      const uint64_t *peer_schema_hashes,
                                      size_t peer_schema_count,
                                      uint64_t *out_selected_hash)
{
    if (server_schema_hashes == NULL || peer_schema_hashes == NULL || out_selected_hash == NULL) {
        return GLCP_STATUS_INVALID_ARG;
    }

    if (server_schema_count == 0U || peer_schema_count == 0U) {
        return GLCP_STATUS_NO_COMMON_SCHEMA;
    }

    for (size_t s_idx = 0U; s_idx < server_schema_count; ++s_idx) {
        for (size_t p_idx = 0U; p_idx < peer_schema_count; ++p_idx) {
            if (server_schema_hashes[s_idx] == peer_schema_hashes[p_idx]) {
                *out_selected_hash = server_schema_hashes[s_idx];
                return GLCP_STATUS_OK;
            }
        }
    }

    return GLCP_STATUS_NO_COMMON_SCHEMA;
}
