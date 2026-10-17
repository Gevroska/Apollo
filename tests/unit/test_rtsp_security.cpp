#include "../tests_common.h"
#include <src/protocol_test.h>
#include <src/config.h>
#include <boost/asio.hpp>
#include <cstring>
#include <map>
#include <limits>

using namespace rtsp_stream;

namespace {
  std::shared_ptr<launch_session_t> launch(bool encrypted = true) {
    auto session = std::make_shared<launch_session_t>();
    session->id = 42;
    session->fps = 60000;
    session->gcm_key = crypto::aes_t(16, 0x22);
    if (encrypted) session->rtsp_cipher.emplace(session->gcm_key, false);
    session->rtsp_iv_counter = 0;
    return session;
  }
  std::string seal_message(const std::string &plaintext, uint32_t sequence, unsigned char key_byte = 0x22) {
    std::string packet(24 + plaintext.size(), '\0');
    auto size = util::endian::big<uint32_t>(0x80000000U | static_cast<uint32_t>(plaintext.size()));
    auto seq = util::endian::big<uint32_t>(sequence);
    std::memcpy(packet.data(), &size, 4);
    std::memcpy(packet.data() + 4, &seq, 4);
    crypto::aes_t iv(12);
    std::memcpy(iv.data(), &sequence, 4);
    iv[10] = 'C'; iv[11] = 'R';
    crypto::cipher::gcm_t cipher(crypto::aes_t(16, key_byte), false);
    auto length = cipher.encrypt(plaintext, reinterpret_cast<uint8_t *>(packet.data() + 8),
                                 reinterpret_cast<uint8_t *>(packet.data() + 24), &iv);
    // The tag is written separately; this API returns the ciphertext length.
    EXPECT_EQ(length, plaintext.size());
    return packet;
  }
  std::string seal(std::string command, uint32_t sequence, unsigned char key_byte = 0x22) {
    return seal_message(command + " / RTSP/1.0\r\nCSeq: 1\r\n\r\n", sequence, key_byte);
  }
  std::string open_response(const std::string &packet) {
    if (packet.size() < 24) { ADD_FAILURE() << "Missing encrypted response"; return {}; }
    uint32_t wire_sequence;
    std::memcpy(&wire_sequence, packet.data() + 4, 4);
    auto sequence = util::endian::big(wire_sequence);
    crypto::aes_t iv(12);
    std::memcpy(iv.data(), &sequence, 4);
    iv[10] = 'H'; iv[11] = 'R';
    crypto::cipher::gcm_t cipher(crypto::aes_t(16, 0x22), false);
    std::vector<uint8_t> plaintext;
    EXPECT_EQ(cipher.decrypt(std::string_view(packet).substr(8), plaintext, &iv), 0);
    return {plaintext.begin(), plaintext.end()};
  }
  std::string announce_payload(const std::string &field, const std::string &value) {
    std::map<std::string, std::string> attributes {
      {"x-nv-audio.surround.numChannels", "2"}, {"x-nv-audio.surround.channelMask", "3"},
      {"x-nv-audio.surround.AudioQuality", "1"}, {"x-nv-video[0].packetSize", "1024"},
      {"x-nv-video[0].clientViewportHt", "1080"}, {"x-nv-video[0].clientViewportWd", "1920"},
      {"x-nv-video[0].maxFPS", "60"}, {"x-nv-vqos[0].bw.maximumBitrateKbps", "10000"},
      {"x-nv-video[0].videoEncoderSlicesPerFrame", "1"}, {"x-nv-video[0].maxNumReferenceFrames", "1"}
    };
    if (!field.empty()) attributes[field] = value;
    std::string payload = "s=Moonlight\r\na=recvonly\r\n";
    for (const auto &[name, text] : attributes) payload += "a=" + name + ":" + text + "\r\n";
    // SDP does not require the final line to have a trailing newline.
    payload.resize(payload.size() - 2);
    return payload;
  }
  std::string announce(const std::string &payload, uint32_t sequence) {
    return seal_message("ANNOUNCE / RTSP/1.0\r\nCSeq: 1\r\nContent-Length: " +
                        std::to_string(payload.size()) + "\r\n\r\n" + payload, sequence);
  }
  void rejected(const std::vector<std::string> &packets) {
    auto session = launch();
    auto result = test::exchange(session, packets);
    EXPECT_TRUE(result.commands.empty());
    EXPECT_EQ(result.response_counter, 0);
    EXPECT_TRUE(result.pending);
    for (auto &response : result.responses) EXPECT_TRUE(response.empty());
  }
}

TEST(RtspSecurity, PlaintextLaunchCannotBeTakenOver) {
  auto result = test::exchange(launch(false), {"ANNOUNCE / RTSP/1.0\r\nCSeq: 1\r\n\r\n"});
  EXPECT_TRUE(result.commands.empty());
  EXPECT_FALSE(result.pending);
}
TEST(RtspSecurity, PlaintextDoesNotPoisonEncryptedLaunch) {
  rejected({"OPTIONS / RTSP/1.0\r\nCSeq: 1\r\n\r\n"});
}
TEST(RtspSecurity, RejectsInvalidTagAndWrongKey) {
  auto packet = seal("OPTIONS", 1); packet[8] ^= 1;
  rejected({packet, seal("OPTIONS", 2, 0x11)});
}
TEST(RtspSecurity, RejectsTamperedSequence) {
  auto packet = seal("OPTIONS", 1); packet[7] ^= 1;
  rejected({packet});
}
TEST(RtspSecurity, RejectsIncompleteAndOversizedFrames) {
  auto packet = seal("OPTIONS", 1);
  rejected({packet.substr(0, 12), packet.substr(0, packet.size() - 1)});
  uint32_t size = util::endian::big<uint32_t>(0xffffffffU);
  std::memcpy(packet.data(), &size, 4);
  rejected({packet});
}
TEST(RtspSecurity, InvalidHighSequenceDoesNotReserveCounter) {
  auto bad = seal("OPTIONS", std::numeric_limits<uint32_t>::max()); bad[8] ^= 1;
  auto result = test::exchange(launch(), {bad, seal("OPTIONS", 1)});
  ASSERT_EQ(result.commands, std::vector<std::string> {"OPTIONS"});
  EXPECT_EQ(result.response_counter, 1);
  EXPECT_TRUE(result.responses[0].empty());
  EXPECT_FALSE(result.responses[1].empty());
}
TEST(RtspSecurity, MultipleSocketsPreserveHandshakeAndPlay) {
  auto result = test::exchange(launch(), {seal("OPTIONS", 1), seal("DESCRIBE", 2),
    seal("SETUP", 3), seal("SETUP", 4), seal("ANNOUNCE", 5), seal("PLAY", 6)});
  EXPECT_EQ(result.commands, (std::vector<std::string> {"OPTIONS", "DESCRIBE", "SETUP", "SETUP", "ANNOUNCE", "PLAY"}));
  EXPECT_EQ(result.response_counter, 6);
  EXPECT_TRUE(result.pending);
}
TEST(RtspSecurity, ReplayCannotDispatchTwice) {
  auto packet = seal("OPTIONS", 1);
  auto result = test::exchange(launch(), {packet, packet});
  EXPECT_EQ(result.commands.size(), 1);
  EXPECT_EQ(result.response_counter, 1);
  EXPECT_TRUE(result.responses[1].empty());
}
TEST(RtspSecurity, DuplicateAnnounceDoesNotAllocateAgainAndPlayWorks) {
  auto result = test::exchange(launch(), {seal("ANNOUNCE", 1), seal("ANNOUNCE", 2), seal("PLAY", 3)});
  EXPECT_EQ(result.commands, (std::vector<std::string> {"ANNOUNCE", "PLAY"}));
  EXPECT_EQ(result.response_counter, 3);
}
TEST(RtspSecurity, AcceptedSocketCannotOutliveItsLaunch) {
  for (auto retirement : {test::retirement::clear, test::retirement::expire, test::retirement::replace, test::retirement::terminate}) {
    auto result = test::exchange(launch(), {seal("OPTIONS", 1)}, retirement);
    EXPECT_TRUE(result.commands.empty());
    EXPECT_EQ(result.response_counter, 0);
    EXPECT_TRUE(result.responses[0].empty());
    if (retirement != test::retirement::replace) EXPECT_FALSE(result.pending);
  }
}
TEST(RtspSecurity, WrongClearIdPreservesLegitimateLaunch) {
  auto result = test::exchange(launch(), {seal("OPTIONS", 1)}, test::retirement::wrong_id);
  EXPECT_EQ(result.commands.size(), 1);
  EXPECT_TRUE(result.pending);
}
TEST(RtspSecurity, AuthenticatedResponseIsNotDispatchedAsRequest) {
  auto packet = seal("RTSP/1.0", 1);
  auto result = test::exchange(launch(), {packet});
  EXPECT_TRUE(result.commands.empty());
}

TEST(RtspSecurity, ProductionSetupRejectsMissingOrEmptyStreamTarget) {
  auto result = test::exchange(launch(), {
    seal_message("SETUP / RTSP/1.0\r\nCSeq: 1\r\n\r\n", 1),
    seal_message("SETUP streamid= RTSP/1.0\r\nCSeq: 2\r\n\r\n", 2)
  }, test::retirement::none, true);
  ASSERT_EQ(result.responses.size(), 2);
  for (const auto &response : result.responses) EXPECT_NE(open_response(response).find("400 BAD REQUEST"), std::string::npos);
}

TEST(RtspSecurity, ProductionAnnounceRejectsMalformedAttributes) {
  std::vector<std::string> packets;
  for (const auto &payload : {"a=x-nv-audio.surround.numChannels:\r\n",
                              "a=x-nv-audio.surround.numChannels:   \r\n", "a=:\r\n", "a=\r\n"}) {
    const auto message = std::string("ANNOUNCE / RTSP/1.0\r\nCSeq: 1\r\nContent-Length: ") +
      std::to_string(std::strlen(payload)) + "\r\n\r\n" + payload;
    packets.push_back(seal_message(message, static_cast<uint32_t>(packets.size() + 1)));
  }
  auto result = test::exchange(launch(), packets, test::retirement::none, true);
  ASSERT_EQ(result.responses.size(), packets.size());
  for (const auto &response : result.responses) EXPECT_NE(open_response(response).find("400 BAD REQUEST"), std::string::npos);
}

TEST(RtspSecurity, ProductionSetupPreservesAllStreamTypes) {
  std::vector<std::string> packets;
  for (const auto &type : {"audio", "video", "control"}) {
    packets.push_back(seal_message(std::string("SETUP streamid=") + type + "/0 RTSP/1.0\r\nCSeq: 1\r\n\r\n",
                                  static_cast<uint32_t>(packets.size() + 1)));
  }
  auto session = launch();
  session->av_ping_payload = "0123456789abcdef";
  session->control_connect_data = 1234;
  auto result = test::exchange(session, packets, test::retirement::none, true);
  ASSERT_EQ(result.responses.size(), 3);
  for (const auto &response : result.responses) EXPECT_NE(open_response(response).find("200 OK"), std::string::npos);
  EXPECT_NE(open_response(result.responses[0]).find("X-SS-Ping-Payload: 0123456789abcdef"), std::string::npos);
  EXPECT_NE(open_response(result.responses[2]).find("X-SS-Connect-Data: 1234"), std::string::npos);
}

TEST(RtspSecurity, ProductionAnnounceRejectsUnsafeNumericConfiguration) {
  const auto previous_mode = config::stream.lan_encryption_mode;
  auto restore = util::fail_guard([&] { config::stream.lan_encryption_mode = previous_mode; });
  // A malformed case that slips through validation still cannot start hardware.
  config::stream.lan_encryption_mode = config::ENCRYPTION_MODE_MANDATORY;
  std::vector<std::string> packets;
  for (const auto &[name, value] : std::map<std::string, std::string> {
      {"x-nv-video[0].packetSize", "16"}, {"x-nv-video[0].maxFPS", "2147483647"},
      {"x-nv-audio.surround.numChannels", "7"}, {"x-nv-video[0].clientViewportWd", "-1"},
      {"x-nv-vqos[0].bw.maximumBitrateKbps", "9999999999999999999999999999999999"},
      {"x-nv-aqos.packetDuration", "0"}, {"x-nv-video[0].maxNumReferenceFrames", "17"},
      {"x-nv-video[0].videoEncoderSlicesPerFrame", "one"}}) {
    packets.push_back(announce(announce_payload(name, value), static_cast<uint32_t>(packets.size() + 1)));
  }
  auto result = test::exchange(launch(), packets, test::retirement::none, true);
  ASSERT_EQ(result.responses.size(), packets.size());
  for (const auto &response : result.responses) EXPECT_NE(open_response(response).find("400 BAD REQUEST"), std::string::npos);
}

TEST(RtspSecurity, ProductionAnnouncePreservesIntegerAndFractionalFrameRates) {
  const auto previous_mode = config::stream.lan_encryption_mode;
  auto restore = util::fail_guard([&] { config::stream.lan_encryption_mode = previous_mode; });
  config::stream.lan_encryption_mode = config::ENCRYPTION_MODE_MANDATORY;
  std::vector<std::string> packets;
  for (const auto &fps : {"60", "59940", "240"}) {
    packets.push_back(announce(announce_payload("x-nv-video[0].maxFPS", fps), static_cast<uint32_t>(packets.size() + 1)));
  }
  auto result = test::exchange(launch(), packets, test::retirement::none, true);
  ASSERT_EQ(result.responses.size(), packets.size());
  // Reaching the encryption policy proves the real SDP parser accepted these
  // configurations without allocating an encoder, display or input device.
  for (const auto &response : result.responses) EXPECT_NE(open_response(response).find("403 Forbidden"), std::string::npos);
}

TEST(RtspSecurity, RevocationClosesIdleAndPartialConnections) {
  for (const bool expire : {false, true}) {
    for (const bool partial : {false, true}) EXPECT_TRUE(test::stalled_sockets_close(expire, partial));
  }
}

TEST(RtspSecurity, PendingSocketCapacityIsBoundedAndReusable) {
  EXPECT_TRUE(test::socket_capacity_recovers());
}

TEST(RtspSecurity, ControlRetirementDoesNotBlockSessionCleanup) {
  EXPECT_TRUE(test::control_retirement_does_not_block_cleanup());
}
