#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum si_message_type {
    SI_MESSAGE_RESET = 1,
    SI_MESSAGE_INITIALIZE = 2,
    SI_MESSAGE_CONNECT = 3,
    SI_MESSAGE_DISCONNECT = 4,
    SI_MESSAGE_KEEPALIVE = 5,
    SI_MESSAGE_ERROR = 6,
    SI_MESSAGE_ACK = 7,
    SI_MESSAGE_DATA = 8,
};

enum si_decode_status {
    SI_DECODE_OK = 0,
    SI_DECODE_NEED_MORE_DATA = 1,
    SI_DECODE_CRC_MISMATCH = 2,
    SI_DECODE_UNSUPPORTED_MESSAGE_TYPE = 3,
};

struct si_frame {
    uint8_t message_type;
    uint32_t host_live_integer;
    uint32_t device_live_integer;
    uint16_t sequence;
    const uint8_t* payload;
    size_t payload_length;
};

uint16_t si_crc16_ccitt(const uint8_t* data, size_t length);
size_t si_encode_frame(const struct si_frame* frame, uint8_t* out_buffer, size_t out_capacity);
int si_decode_one_frame(
    uint8_t* io_buffer,
    size_t* io_length,
    struct si_frame* out_frame,
    uint8_t* out_payload_buffer,
    size_t out_payload_capacity);

#ifdef __cplusplus
}
#endif

