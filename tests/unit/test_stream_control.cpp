#include "../tests_common.h"
#include <src/protocol_test.h>

TEST(StreamControlSecurity, ReleasesControlLocksBeforeRetiringRtspLaunch) {
  EXPECT_TRUE(stream::test::control_session_lock_order(true));
}
TEST(StreamControlSecurity, WrongConnectDataCannotRetireLaunch) {
  EXPECT_TRUE(stream::test::control_session_lock_order(false));
}

TEST(StreamControlSecurity, ModernMediaReceivesKeepPayloadAndSenderTogether) {
  EXPECT_TRUE(stream::test::media_endpoint_isolation(false));
}
TEST(StreamControlSecurity, LegacyMediaReceivesKeepPayloadAndSenderTogether) {
  EXPECT_TRUE(stream::test::media_endpoint_isolation(true));
}

TEST(StreamControlSecurity, ShutdownDrainsPendingReceives) {
  EXPECT_TRUE(stream::test::media_receive_shutdown(false));
}

TEST(StreamControlSecurity, ShutdownBeforeReceiverStartsStillDrainsReceives) {
  EXPECT_TRUE(stream::test::media_receive_shutdown(true));
}
