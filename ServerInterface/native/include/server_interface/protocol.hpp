#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace server_interface {

constexpr std::uint8_t kSofByte0 = 0xA5;
constexpr std::uint8_t kSofByte1 = 0x5A;
constexpr std::size_t kHeaderSize = 15;
constexpr std::size_t kCrcSize = 2;

enum class MessageType : std::uint8_t {
    Reset = 1,
    Initialize = 2,
    Connect = 3,
    Disconnect = 4,
    Keepalive = 5,
    Error = 6,
    Ack = 7,
    Data = 8,
};

struct Frame {
    MessageType message_type;
    std::uint32_t host_live_integer;
    std::uint32_t device_live_integer;
    std::uint16_t sequence;
    std::vector<std::uint8_t> payload;
};

enum class DecodeStatus : std::uint8_t {
    Ok = 0,
    NeedMoreData = 1,
    CrcMismatch = 2,
    UnsupportedMessageType = 3,
};

std::uint16_t crc16_ccitt(const std::uint8_t* data, std::size_t length);
std::vector<std::uint8_t> encode_frame(const Frame& frame);
DecodeStatus decode_one_frame(std::vector<std::uint8_t>& buffer, Frame& out_frame);

}  // namespace server_interface

