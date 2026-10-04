#include "../tests_common.h"
#include <src/nvhttp.h>
#include <src/httpcommon.h>

TEST(LaunchProtocol, RecentClientNegotiatesExistingEncryptedRtsp) {
  crypto::named_cert_t client {};
  client.uuid = "client";
  client.name = "test";
  client.perm = crypto::PERM::_all;
  nvhttp::args_t args {
    {"rikey", "22222222222222222222222222222222"},
    {"rikeyid", "1"}, {"corever", "1"}, {"mode", "1920x1080x60"}
  };
  auto session = nvhttp::make_launch_session(false, false, args, &client);
  ASSERT_TRUE(session->rtsp_cipher);
  EXPECT_EQ(session->gcm_key, crypto::aes_t(16, 0x22));
  EXPECT_EQ(session->rtsp_url_scheme, "rtspenc://");
  EXPECT_EQ(session->width, 1920);
  EXPECT_EQ(session->height, 1080);
  EXPECT_EQ(session->fps, 60000);
  EXPECT_EQ(session->perm, client.perm);
}
TEST(LaunchProtocol, WebUiLaunchDoesNotRequireClientHandshakeParameters) {
  crypto::named_cert_t host {};
  host.uuid = http::unique_id;
  host.perm = crypto::PERM::_all;
  nvhttp::args_t args {{"mode", "1920x1080x60"}};
  auto session = nvhttp::make_launch_session(false, false, args, &host);
  ASSERT_TRUE(session);
  EXPECT_FALSE(session->rtsp_cipher);
  EXPECT_TRUE(session->gcm_key.empty());
  EXPECT_EQ(session->width, 1920);
}
