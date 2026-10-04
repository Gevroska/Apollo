#include "../tests_common.h"
#include <src/protocol_test.h>
#include <src/config.h>
#include <boost/asio.hpp>
#include <future>
#include <boost/property_tree/xml_parser.hpp>

extern const std::string PRIVATE_KEY;
extern const std::string PUBLIC_CERT;

namespace {
  using tcp = boost::asio::ip::tcp;
  struct PairingHttp : testing::Test {
    SimpleWeb::Server<SimpleWeb::HTTP> server;
    std::thread worker;
    boost::asio::io_context clients_io;
    unsigned short port = 0;
    std::vector<std::shared_ptr<tcp::socket>> clients;
    void SetUp() override {
      nvhttp::test::reset();
      nvhttp::setup(PRIVATE_KEY, PUBLIC_CERT);
      config::sunshine.enable_pairing = true;
      config::sunshine.flags[config::flag::PIN_STDIN] = false;
      server.config.address = "127.0.0.1";
      server.config.port = 0;
      server.config.thread_pool_size = 4;
      server.config.max_request_streambuf_size = 65536;
      server.resource["^/pair$"]["GET"] = nvhttp::test::pair_http;
      std::promise<unsigned short> ready;
      auto future = ready.get_future();
      worker = std::thread([&] { server.start([&](unsigned short value) { ready.set_value(value); }); });
      port = future.get();
    }
    void TearDown() override {
      server.stop();
      worker.join();
      nvhttp::test::reset();
      clients.clear();
      config::sunshine.enable_pairing = true;
    }
    std::string query(std::string id, std::string suffix = "") {
      return "uniqueid=" + id + "&phrase=getservercert&devicename=test&clientcert=" +
        util::hex_vec(PUBLIC_CERT, true) + "&salt=ff5dc6eda99339a8a0793e216c4257c4" + suffix;
    }
    std::shared_ptr<tcp::socket> begin(const std::string &query, unsigned int source = 1) {
      auto client = std::make_shared<tcp::socket>(clients_io);
      client->open(tcp::v4());
      client->bind({boost::asio::ip::make_address("127.0.0." + std::to_string(source)), 0});
      client->connect({boost::asio::ip::address_v4::loopback(), port});
      std::string request = "GET /pair?" + query + " HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n";
      boost::asio::write(*client, boost::asio::buffer(request));
      client->non_blocking(true);
      clients.push_back(client);
      return client;
    }
    std::string read(const std::shared_ptr<tcp::socket> &client) {
      std::string result;
      std::array<char, 4096> buffer;
      auto deadline = std::chrono::steady_clock::now() + 2s;
      while (std::chrono::steady_clock::now() < deadline) {
        boost::system::error_code ec;
        auto size = client->read_some(boost::asio::buffer(buffer), ec);
        if (!ec) result.append(buffer.data(), size);
        else if (ec != boost::asio::error::would_block && ec != boost::asio::error::try_again) return result;
        std::this_thread::sleep_for(1ms);
      }
      ADD_FAILURE() << "HTTP response did not close";
      return result;
    }
    boost::property_tree::ptree xml(const std::string &body) {
      auto start = body.find("\r\n\r\n");
      std::istringstream stream(body.substr(start + 4));
      boost::property_tree::ptree tree;
      boost::property_tree::read_xml(stream, tree);
      return tree;
    }
    bool finish_pair(const std::string &id, const std::string &pin) {
      auto salt = util::from_hex<std::array<uint8_t, 16>>("ff5dc6eda99339a8a0793e216c4257c4", true);
      auto key = crypto::gen_aes_key(salt, pin);
      crypto::cipher::ecb_t cipher(key, false);
      auto challenge = crypto::rand(16);
      std::vector<uint8_t> encrypted;
      cipher.encrypt(challenge, encrypted);
      auto phase2 = xml(read(begin("uniqueid=" + id + "&clientchallenge=" + util::hex_vec(encrypted, true))));
      auto response = util::from_hex_vec(phase2.get<std::string>("root.challengeresponse"), true);
      std::vector<uint8_t> decoded;
      cipher.decrypt(response, decoded);
      if (decoded.size() != 48) return false;
      auto cert = crypto::x509(PUBLIC_CERT);
      auto signature = crypto::signature(cert);
      auto secret = crypto::rand(16);
      std::string proof(reinterpret_cast<char *>(decoded.data() + 32), 16);
      proof.append(signature);
      proof.append(secret);
      auto hash = crypto::hash(proof);
      cipher.encrypt(std::string_view(reinterpret_cast<char *>(hash.data()), hash.size()), encrypted);
      auto phase3 = xml(read(begin("uniqueid=" + id + "&serverchallengeresp=" + util::hex_vec(encrypted, true))));
      if (phase3.get<int>("root.paired") != 1) return false;
      auto signed_secret = crypto::sign256(crypto::pkey(PRIVATE_KEY), secret);
      secret.append(reinterpret_cast<char *>(signed_secret.data()), signed_secret.size());
      auto phase4 = xml(read(begin("uniqueid=" + id + "&clientpairingsecret=" + util::hex_vec(secret, true))));
      return phase4.get<int>("root.paired") == 1;
    }
    void wait_pending(std::size_t count) {
      auto deadline = std::chrono::steady_clock::now() + 2s;
      while (nvhttp::test::pending() != count && std::chrono::steady_clock::now() < deadline)
        std::this_thread::sleep_for(1ms);
      ASSERT_EQ(nvhttp::test::pending(), count);
    }
  };
}

TEST_F(PairingHttp, InvalidOtpRetainsNoState) {
  auto pin = nvhttp::request_otp("passphrase", "test");
  ASSERT_FALSE(pin.empty());
  for (int i = 0; i < 5; ++i) {
    auto body = read(begin(query("invalid" + std::to_string(i), "&otpauth=" + std::string(64, '0'))));
    EXPECT_NE(body.find("paired>1"), std::string::npos); // Keep the non-oracle response.
    EXPECT_EQ(nvhttp::test::pending(), 0);
  }
}
TEST_F(PairingHttp, ValidOtpProgressesAndCannotBeReused) {
  auto pin = nvhttp::request_otp("passphrase", "test");
  auto hash = util::hex(crypto::hash(pin + "ff5dc6eda99339a8a0793e216c4257c4" + "passphrase"), true);
  auto suffix = "&otpauth=" + std::string(hash.to_string_view());
  EXPECT_NE(read(begin(query("good", suffix))).find("paired>1"), std::string::npos);
  EXPECT_EQ(nvhttp::test::pending(), 1);
  read(begin(query("reuse", suffix)));
  EXPECT_EQ(nvhttp::test::pending(), 1);
  auto bad = read(begin("uniqueid=good&clientchallenge=00"));
  EXPECT_NE(bad.find("status_code"), std::string::npos);
}
TEST_F(PairingHttp, PinCanBeCorrectedAndAdvancesOldestPendingRequest) {
  auto first = begin(query("pin1")); wait_pending(1);
  auto second = begin(query("pin2")); wait_pending(2);
  EXPECT_FALSE(nvhttp::pin("xx", ""));
  EXPECT_TRUE(nvhttp::pin("5338", "first"));
  EXPECT_NE(read(first).find("paired>1"), std::string::npos);
  EXPECT_TRUE(nvhttp::pin("5338", "second"));
  EXPECT_NE(read(second).find("paired>1"), std::string::npos);
  EXPECT_FALSE(nvhttp::pin("5338", ""));
}
TEST_F(PairingHttp, DuplicateIdCannotReplacePendingSaltOrResponse) {
  auto original = begin(query("same")); wait_pending(1);
  EXPECT_NE(read(begin(query("same"))).find("status_code=\"409\""), std::string::npos);
  EXPECT_EQ(nvhttp::test::pending(), 1);
  ASSERT_TRUE(nvhttp::pin("5338", ""));
  EXPECT_NE(read(original).find("paired>1"), std::string::npos);
}
TEST_F(PairingHttp, SourceQuotaAndRateAreBounded) {
  for (int i = 0; i < 4; ++i) { begin(query("source" + std::to_string(i))); wait_pending(i + 1); }
  EXPECT_NE(read(begin(query("overflow"))).find("status_code=\"429\""), std::string::npos);
  EXPECT_EQ(nvhttp::test::pending(), 4);
  nvhttp::test::expire();
  for (int i = 0; i < 5; ++i) read(begin(query("otp" + std::to_string(i), "&otpauth=" + std::string(64, '0'))));
  EXPECT_EQ(nvhttp::test::pending(), 0);
  EXPECT_EQ(nvhttp::test::rate_sources(), 1);
}
TEST_F(PairingHttp, GlobalStateCap) {
  for (int i = 0; i < 32; ++i) {
    begin(query("global" + std::to_string(i)), 1 + i / 4);
    wait_pending(i + 1);
  }
  EXPECT_NE(read(begin(query("globaloverflow"), 20)).find("status_code=\"429\""), std::string::npos);
  EXPECT_EQ(nvhttp::test::pending(), 32);
}
TEST_F(PairingHttp, ExpiryReleasesPendingRequestsWithoutNewTraffic) {
  auto client = begin(query("expire")); wait_pending(1);
  nvhttp::test::expire();
  EXPECT_EQ(nvhttp::test::pending(), 0);
  read(client);
  EXPECT_FALSE(nvhttp::pin("5338", ""));
  begin(query("afterexpiry")); wait_pending(1);
}
TEST_F(PairingHttp, MalformedAndOversizedFieldsDoNotAllocate) {
  for (auto query : {"uniqueid=x&phrase=getservercert",
                    "uniqueid=x&uniqueid=y&phrase=getservercert",
                    "uniqueid=x&phrase=getservercert&salt=zz"}) {
    EXPECT_NE(read(begin(query)).find("status_code=\"400\""), std::string::npos);
    EXPECT_EQ(nvhttp::test::pending(), 0);
  }
  EXPECT_NE(read(begin(this->query(std::string(129, 'x')))).find("status_code=\"400\""), std::string::npos);
  EXPECT_EQ(nvhttp::test::pending(), 0);
}
TEST_F(PairingHttp, DisabledPairingDoesNotAllocate) {
  config::sunshine.enable_pairing = false;
  EXPECT_NE(read(begin(query("disabled"))).find("status_code=\"403\""), std::string::npos);
  EXPECT_EQ(nvhttp::test::pending(), 0);
}
TEST_F(PairingHttp, DifferentSourceCannotAdvanceOrErasePendingPair) {
  auto client = begin(query("bound")); wait_pending(1);
  EXPECT_NE(read(begin("uniqueid=bound&clientchallenge=00", 2)).find("status_code=\"400\""), std::string::npos);
  EXPECT_EQ(nvhttp::test::pending(), 1);
  ASSERT_TRUE(nvhttp::pin("5338", ""));
  read(client);
  read(begin("uniqueid=bound&clientchallenge=bad"));
  EXPECT_EQ(nvhttp::test::pending(), 0);
}

TEST_F(PairingHttp, CompletePinHandshakeAuthorizesClientAndReleasesState) {
  auto request = begin(query("completepin")); wait_pending(1);
  ASSERT_TRUE(nvhttp::pin("5338", "complete"));
  EXPECT_NE(read(request).find("paired>1"), std::string::npos);
  ASSERT_TRUE(finish_pair("completepin", "5338"));
  EXPECT_EQ(nvhttp::test::pending(), 0);
  ASSERT_EQ(nvhttp::get_all_clients().size(), 1);
}
TEST_F(PairingHttp, CompleteOtpHandshakeAuthorizesClientAndReleasesState) {
  auto pin = nvhttp::request_otp("passphrase", "complete");
  auto hash = util::hex(crypto::hash(pin + "ff5dc6eda99339a8a0793e216c4257c4" + "passphrase"), true);
  read(begin(query("completeotp", "&otpauth=" + std::string(hash.to_string_view()))));
  ASSERT_TRUE(finish_pair("completeotp", pin));
  EXPECT_EQ(nvhttp::test::pending(), 0);
  ASSERT_EQ(nvhttp::get_all_clients().size(), 1);
}
TEST_F(PairingHttp, WrongPinHandshakeCannotAuthorizeAndReleasesState) {
  auto request = begin(query("wrongpin")); wait_pending(1);
  ASSERT_TRUE(nvhttp::pin("5338", ""));
  read(request);
  EXPECT_FALSE(finish_pair("wrongpin", "1234"));
  EXPECT_EQ(nvhttp::test::pending(), 0);
  EXPECT_TRUE(nvhttp::get_all_clients().empty());
}

TEST_F(PairingHttp, SourceRateCacheCannotGrowWithoutBound) {
  for (unsigned int source = 1; source <= 64; ++source) {
    read(begin(query("rate" + std::to_string(source), "&otpauth=" + std::string(64, '0')), source));
  }
  EXPECT_EQ(nvhttp::test::rate_sources(), 64);
  EXPECT_EQ(nvhttp::test::pending(), 0);
  EXPECT_NE(read(begin(query("sourceoverflow"), 65)).find("status_code=\"429\""), std::string::npos);
  EXPECT_EQ(nvhttp::test::rate_sources(), 64);
}

TEST_F(PairingHttp, ConcurrentInvalidOtpAttemptsKeepStateBounded) {
  std::vector<std::shared_ptr<tcp::socket>> attempts;
  for (unsigned int i = 0; i < 32; ++i) {
    attempts.push_back(begin(query("concurrent" + std::to_string(i), "&otpauth=" + std::string(64, '0'))));
  }
  for (auto &request : attempts) read(request);
  EXPECT_EQ(nvhttp::test::pending(), 0);
  EXPECT_EQ(nvhttp::test::rate_sources(), 1);
}
