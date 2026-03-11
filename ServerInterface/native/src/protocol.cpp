#include "server_interface/c_api.h"
#include "server_interface/protocol.hpp"

#include <algorithm>
#include <array>
#include <cstring>
#include <vector>

namespace server_interface {

std::uint16_t crc16_ccitt(const std::uint8_t* data, std::size_t length) {
    std::uint16_t crc = 0xFFFF;
    for (std::size_t index = 0; index < length; ++index) {
        crc ^= static_cast<std::uint16_t>(data[index]) << 8;
        for (int bit = 0; bit < 8; ++bit) {
            if ((crc & 0x8000U) != 0U) {
                crc = static_cast<std::uint16_t>(((crc << 1U) ^ 0x1021U) & 0xFFFFU);
            } else {
                crc = static_cast<std::uint16_t>((crc << 1U) & 0xFFFFU);
            }
        }
    }
    return crc;
}

std::vector<std::uint8_t> encode_frame(const Frame& frame) {
    const std::size_t payload_length = frame.payload.size();
    std::vector<std::uint8_t> output(kHeaderSize + payload_length + kCrcSize);

    output[0] = kSofByte0;
    output[1] = kSofByte1;
    output[2] = static_cast<std::uint8_t>(frame.message_type);
    output[3] = static_cast<std::uint8_t>(payload_length & 0xFFU);
    output[4] = static_cast<std::uint8_t>((payload_length >> 8U) & 0xFFU);
    output[5] = static_cast<std::uint8_t>(frame.host_live_integer & 0xFFU);
    output[6] = static_cast<std::uint8_t>((frame.host_live_integer >> 8U) & 0xFFU);
    output[7] = static_cast<std::uint8_t>((frame.host_live_integer >> 16U) & 0xFFU);
    output[8] = static_cast<std::uint8_t>((frame.host_live_integer >> 24U) & 0xFFU);
    output[9] = static_cast<std::uint8_t>(frame.device_live_integer & 0xFFU);
    output[10] = static_cast<std::uint8_t>((frame.device_live_integer >> 8U) & 0xFFU);
    output[11] = static_cast<std::uint8_t>((frame.device_live_integer >> 16U) & 0xFFU);
    output[12] = static_cast<std::uint8_t>((frame.device_live_integer >> 24U) & 0xFFU);
    output[13] = static_cast<std::uint8_t>(frame.sequence & 0xFFU);
    output[14] = static_cast<std::uint8_t>((frame.sequence >> 8U) & 0xFFU);

    if (payload_length > 0U) {
        std::copy(frame.payload.begin(), frame.payload.end(), output.begin() + static_cast<std::ptrdiff_t>(kHeaderSize));
    }

    const std::uint16_t crc = crc16_ccitt(output.data(), output.size() - kCrcSize);
    output[output.size() - 2U] = static_cast<std::uint8_t>(crc & 0xFFU);
    output[output.size() - 1U] = static_cast<std::uint8_t>((crc >> 8U) & 0xFFU);
    return output;
}

DecodeStatus decode_one_frame(std::vector<std::uint8_t>& buffer, Frame& out_frame) {
    constexpr std::array<std::uint8_t, 2> sof = {kSofByte0, kSofByte1};
    auto sof_pos = std::search(buffer.begin(), buffer.end(), sof.begin(), sof.end());
    if (sof_pos == buffer.end()) {
        buffer.clear();
        return DecodeStatus::NeedMoreData;
    }
    if (sof_pos != buffer.begin()) {
        buffer.erase(buffer.begin(), sof_pos);
    }

    if (buffer.size() < kHeaderSize + kCrcSize) {
        return DecodeStatus::NeedMoreData;
    }

    const std::size_t payload_length = static_cast<std::size_t>(buffer[3]) |
        (static_cast<std::size_t>(buffer[4]) << 8U);
    const std::size_t total_length = kHeaderSize + payload_length + kCrcSize;
    if (buffer.size() < total_length) {
        return DecodeStatus::NeedMoreData;
    }

    const std::uint16_t expected_crc = static_cast<std::uint16_t>(buffer[total_length - 2U]) |
        (static_cast<std::uint16_t>(buffer[total_length - 1U]) << 8U);
    const std::uint16_t actual_crc = crc16_ccitt(buffer.data(), total_length - kCrcSize);
    if (expected_crc != actual_crc) {
        buffer.erase(buffer.begin(), buffer.begin() + 2);
        return DecodeStatus::CrcMismatch;
    }

    const auto raw_type = static_cast<MessageType>(buffer[2]);
    switch (raw_type) {
    case MessageType::Reset:
    case MessageType::Initialize:
    case MessageType::Connect:
    case MessageType::Disconnect:
    case MessageType::Keepalive:
    case MessageType::Error:
    case MessageType::Ack:
    case MessageType::Data:
        break;
    default:
        buffer.erase(buffer.begin(), buffer.begin() + static_cast<std::ptrdiff_t>(total_length));
        return DecodeStatus::UnsupportedMessageType;
    }

    out_frame.message_type = raw_type;
    out_frame.host_live_integer = static_cast<std::uint32_t>(buffer[5]) |
        (static_cast<std::uint32_t>(buffer[6]) << 8U) |
        (static_cast<std::uint32_t>(buffer[7]) << 16U) |
        (static_cast<std::uint32_t>(buffer[8]) << 24U);
    out_frame.device_live_integer = static_cast<std::uint32_t>(buffer[9]) |
        (static_cast<std::uint32_t>(buffer[10]) << 8U) |
        (static_cast<std::uint32_t>(buffer[11]) << 16U) |
        (static_cast<std::uint32_t>(buffer[12]) << 24U);
    out_frame.sequence = static_cast<std::uint16_t>(buffer[13]) |
        (static_cast<std::uint16_t>(buffer[14]) << 8U);
    out_frame.payload.assign(
        buffer.begin() + static_cast<std::ptrdiff_t>(kHeaderSize),
        buffer.begin() + static_cast<std::ptrdiff_t>(total_length - kCrcSize));

    buffer.erase(buffer.begin(), buffer.begin() + static_cast<std::ptrdiff_t>(total_length));
    return DecodeStatus::Ok;
}

}  // namespace server_interface

extern "C" {

uint16_t si_crc16_ccitt(const uint8_t* data, size_t length) {
    return server_interface::crc16_ccitt(data, length);
}

size_t si_encode_frame(const struct si_frame* frame, uint8_t* out_buffer, size_t out_capacity) {
    if (frame == nullptr || out_buffer == nullptr) {
        return 0U;
    }

    const auto* payload_begin = frame->payload;
    const auto* payload_end = frame->payload + frame->payload_length;
    server_interface::Frame cpp_frame{
        static_cast<server_interface::MessageType>(frame->message_type),
        frame->host_live_integer,
        frame->device_live_integer,
        frame->sequence,
        std::vector<std::uint8_t>(payload_begin, payload_end),
    };

    const auto encoded = server_interface::encode_frame(cpp_frame);
    if (encoded.size() > out_capacity) {
        return 0U;
    }

    std::memcpy(out_buffer, encoded.data(), encoded.size());
    return encoded.size();
}

int si_decode_one_frame(
    uint8_t* io_buffer,
    size_t* io_length,
    struct si_frame* out_frame,
    uint8_t* out_payload_buffer,
    size_t out_payload_capacity) {
    if (io_buffer == nullptr || io_length == nullptr || out_frame == nullptr) {
        return static_cast<int>(server_interface::DecodeStatus::NeedMoreData);
    }

    std::vector<std::uint8_t> buffer(io_buffer, io_buffer + *io_length);
    server_interface::Frame frame{
        server_interface::MessageType::Reset,
        0U,
        0U,
        0U,
        {},
    };

    const auto status = server_interface::decode_one_frame(buffer, frame);
    const std::size_t remaining = buffer.size();
    if (remaining > 0U) {
        std::memmove(io_buffer, buffer.data(), remaining);
    }
    *io_length = remaining;

    if (status != server_interface::DecodeStatus::Ok) {
        return static_cast<int>(status);
    }

    if (frame.payload.size() > out_payload_capacity || out_payload_buffer == nullptr) {
        return static_cast<int>(server_interface::DecodeStatus::NeedMoreData);
    }

    if (!frame.payload.empty()) {
        std::memcpy(out_payload_buffer, frame.payload.data(), frame.payload.size());
    }

    out_frame->message_type = static_cast<uint8_t>(frame.message_type);
    out_frame->host_live_integer = frame.host_live_integer;
    out_frame->device_live_integer = frame.device_live_integer;
    out_frame->sequence = frame.sequence;
    out_frame->payload = out_payload_buffer;
    out_frame->payload_length = frame.payload.size();
    return static_cast<int>(status);
}

}  // extern "C"

