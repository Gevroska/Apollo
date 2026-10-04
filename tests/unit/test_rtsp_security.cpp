#include "../tests_common.h"
#include <src/protocol_test.h>
#include <boost/asio.hpp>
#include <limits>

using namespace rtsp_stream;

namespace {
  std::shared_ptr<launch_session_t> launch(bool encrypted = true) {
    auto session = std::make_shared<launch_session_t>();
    session->id = 42;
    session->gcm_key = crypto::aes_t(16, 0x22);
    if (encrypted) session->rtsp_cipher.emplace(session->gcm_key, false);
    session->rtsp_iv_counter = 0;
    return session;
  }
  std::string seal(std::string command, uint32_t sequence, unsigned char key_byte = 0x22) {
    auto plaintext = command + " / RTSP/1.0\r\nCSeq: 1\r\n\r\n";
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
  for (auto retirement : {test::retirement::clear, test::retirement::expire, test::retirement::replace}) {
    auto result = test::exchange(launch(), {seal("OPTIONS", 1)}, retirement);
    EXPECT_TRUE(result.commands.empty());
    EXPECT_EQ(result.response_counter, 0);
    EXPECT_TRUE(result.responses[0].empty());
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
