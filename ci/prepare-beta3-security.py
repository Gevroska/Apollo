"""Apply the two reviewed security fixes to the immutable beta.2 source.

This staging utility is NOT part of the candidate commit. All replacements are
anchored and fail closed if the checked-out source is not the reviewed revision.
"""
from pathlib import Path
import subprocess

BASE = "a8f6a3cf4b5638342964e28658f22c835aa2282c"
if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != BASE:
    raise RuntimeError("Unexpected base revision")
if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
    raise RuntimeError("The candidate checkout must be clean")

changes = {}
def read(path):
    return changes.get(path, Path(path).read_text(encoding="utf-8"))
def replace(path, before, after):
    text = read(path)
    if text.count(before) != 1:
        raise RuntimeError(f"Expected one anchor in {path}: {before[:100]!r}")
    changes[path] = text.replace(before, after, 1)
def section(path, start, end, replacement):
    text = read(path)
    if text.count(start) != 1 or text.count(end) != 1:
        raise RuntimeError(f"Ambiguous section in {path}")
    left = text.index(start)
    right = text.index(end, left)
    changes[path] = text[:left] + replacement + text[right:]
def add(path, content):
    if Path(path).exists() or path in changes:
        raise RuntimeError(f"New file already exists: {path}")
    changes[path] = content

# 1. Each outstanding receive owns its own endpoint, just like its buffer.
path = "src/stream.cpp"
text = read(path)
start = text.index("  void recvThread(broadcast_ctx_t &ctx) {")
end = text.index("  void videoBroadcastThread(udp::socket &sock)", start)
block = text[start:end]
old = "    udp::endpoint peer;"
assert block.count(old) == 1
block = block.replace(old, "    // Never share sender metadata between outstanding audio/video receives.\n    std::array<udp::endpoint, 2> peers;")
old = "      recv_func[buf_elem] = [&, buf_elem](const boost::system::error_code &ec, size_t bytes) {\n        auto fg = util::fail_guard([&]() {\n          sock.async_receive_from(asio::buffer(buf[buf_elem]), peer, 0, recv_func[buf_elem]);\n        });"
new = """      recv_func[buf_elem] = [&, buf_elem](const boost::system::error_code &ec, size_t bytes) {
        // Snapshot the sender paired with this buffer before rearming this lane.
        const auto peer = peers[buf_elem];
        auto fg = util::fail_guard([&]() {
          if (!broadcast_shutdown_event->peek()) {
            sock.async_receive_from(asio::buffer(buf[buf_elem]), peers[buf_elem], 0, recv_func[buf_elem]);
          }
        });"""
assert block.count(old) == 1
block = block.replace(old, new)
for index, lane in enumerate(("video", "audio")):
    old = f"{lane}_sock.async_receive_from(asio::buffer(buf[{index}]), peer, 0, recv_func[{index}]);"
    assert block.count(old) == 1
    block = block.replace(old, old.replace(", peer,", f", peers[{index}],"))
changes[path] = text[:start] + block + text[end:]

# Test hook drives the actual production recvThread, not a parallel mock parser.
media_test_hook = r'''
#ifdef SUNSHINE_TESTS
  namespace test {
    bool media_endpoint_isolation(bool legacy) {
      auto shutdown = mail::man->event<bool>(mail::broadcast_shutdown);
      shutdown->reset();
      broadcast_ctx_t ctx;
      ctx.message_queue_queue = std::make_shared<message_queue_queue_t::element_type>();
      const auto loopback = asio::ip::address_v4::loopback();
      ctx.video_sock.open(udp::v4());
      ctx.audio_sock.open(udp::v4());
      ctx.video_sock.bind({loopback, 0});
      ctx.audio_sock.bind({loopback, 0});
      const auto video_destination = ctx.video_sock.local_endpoint();
      const auto audio_destination = ctx.audio_sock.local_endpoint();
      auto video_queue = std::make_shared<message_queue_t::element_type>();
      auto audio_queue = std::make_shared<message_queue_t::element_type>();
      SS_PING video_ping {};
      SS_PING audio_ping {};
      std::fill(std::begin(video_ping.payload), std::end(video_ping.payload), 'v');
      std::fill(std::begin(audio_ping.payload), std::end(audio_ping.payload), 'a');
      av_session_id_t video_id = legacy ? av_session_id_t {asio::ip::address {loopback}} :
        av_session_id_t {std::string {video_ping.payload, sizeof(video_ping.payload)}};
      av_session_id_t audio_id = legacy ? av_session_id_t {asio::ip::address {loopback}} :
        av_session_id_t {std::string {audio_ping.payload, sizeof(audio_ping.payload)}};
      ctx.message_queue_queue->raise(socket_e::video, video_id, video_queue);
      ctx.message_queue_queue->raise(socket_e::audio, audio_id, audio_queue);
      const std::string video_data = legacy ? "PING" :
        std::string {reinterpret_cast<const char *>(&video_ping), sizeof(video_ping)};
      const std::string audio_data = legacy ? "PING" :
        std::string {reinterpret_cast<const char *>(&audio_ping), sizeof(audio_ping)};
      asio::io_context sender_io;
      udp::socket video_sender(sender_io, udp::endpoint {loopback, 0});
      udp::socket audio_sender(sender_io, udp::endpoint {loopback, 0});
      const auto video_source = video_sender.local_endpoint();
      const auto audio_source = audio_sender.local_endpoint();
      std::exception_ptr receiver_error;
      std::thread worker([&] {
        try { recvThread(ctx); }
        catch (...) { receiver_error = std::current_exception(); }
      });
      auto stop = [&] {
        // Close on the receiver thread and drain cancellation completions before
        // destroying the receive buffers/endpoints. Do not stop io prematurely.
        asio::post(ctx.io_context, [&] {
          shutdown->raise(true);
          boost::system::error_code ignored;
          ctx.video_sock.close(ignored);
          ctx.audio_sock.close(ignored);
        });
        worker.join();
        shutdown->reset();
      };
      auto cleanup = util::fail_guard([&] { if (worker.joinable()) stop(); });
      bool matched = true;
      for (int round = 0; round < 64 && matched; ++round) {
        video_sender.send_to(asio::buffer(video_data), video_destination);
        audio_sender.send_to(asio::buffer(audio_data), audio_destination);
        auto video = video_queue->pop(2s);
        auto audio = audio_queue->pop(2s);
        matched = video && audio && video->first == video_source &&
          audio->first == audio_source && video->second == video_data && audio->second == audio_data;
      }
      stop();
      return matched && !receiver_error;
    }
  }
#endif

'''
replace(path, "  void videoBroadcastThread(udp::socket &sock) {", media_test_hook + "  void videoBroadcastThread(udp::socket &sock) {")
replace("src/protocol_test.h", "  bool control_session_lock_order(bool correct_connect_data);", "  bool control_session_lock_order(bool correct_connect_data);\n  bool media_endpoint_isolation(bool legacy);")
changes["tests/unit/test_stream_control.cpp"] = read("tests/unit/test_stream_control.cpp") + r'''
TEST(StreamControlSecurity, ModernMediaReceivesKeepPayloadAndSenderTogether) {
  EXPECT_TRUE(stream::test::media_endpoint_isolation(false));
}
TEST(StreamControlSecurity, LegacyMediaReceivesKeepPayloadAndSenderTogether) {
  EXPECT_TRUE(stream::test::media_endpoint_isolation(true));
}
'''

# 2. Bind administrator approval to a server-generated, single-use request token.
replace("src/nvhttp.h", "    std::chrono::steady_clock::time_point created = std::chrono::steady_clock::now();", """    std::chrono::steady_clock::time_point created = std::chrono::steady_clock::now();
    // Transient Web UI approval capability: never sent on the GameStream listener.
    std::string approval_token;
    std::string certificate_fingerprint;""")
section("src/nvhttp.h", "  /**\n   * @brief Compare the user supplied pin", "  std::string request_otp", """  /**
   * @brief List pending PIN approvals for the authenticated administrator only.
   * @return Request tokens, untrusted device names, observed IPs and certificate fingerprints.
   */
  nlohmann::json pending_pairings();

  /**
   * @brief Submit a PIN to exactly the pending request selected by the administrator.
   * @param token The server-generated, single-use approval token, not a client uniqueid.
   * @return Whether the PIN was submitted. The client must still finish its handshake.
   */
  bool pin(std::string pin, std::string name, std::string token);

""")
replace("src/nvhttp.cpp", "#include <Simple-Web-Server/server_http.hpp>", "#include <Simple-Web-Server/server_http.hpp>\n#include <openssl/rand.h>")
replace("src/nvhttp.cpp", "      map_id_sess.emplace(uniqID, sess);", r'''      if (!valid_otp && !config::sunshine.flags[config::flag::PIN_STDIN]) {
        std::vector<unsigned char> random_token(32);
        std::vector<unsigned char> fingerprint(EVP_MAX_MD_SIZE);
        unsigned int fingerprint_size = 0;
        auto certificate = crypto::x509(sess->client.cert);
        if (RAND_bytes(random_token.data(), static_cast<int>(random_token.size())) != 1 ||
            !certificate || X509_digest(certificate.get(), EVP_sha256(), fingerprint.data(), &fingerprint_size) != 1 ||
            fingerprint_size != 32) {
          reject(500, "Unable to create pairing approval");
          return;
        }
        fingerprint.resize(fingerprint_size);
        sess->approval_token = util::hex_vec(random_token, true);
        sess->certificate_fingerprint = util::hex_vec(fingerprint, true);
        // Fail closed even on the cryptographically improbable token collision.
        if (std::any_of(map_id_sess.begin(), map_id_sess.end(), [&](const auto &entry) {
              return entry.second->approval_token == sess->approval_token;
            })) {
          reject(500, "Unable to create pairing approval");
          return;
        }
      }
      map_id_sess.emplace(uniqID, sess);''')
section("src/nvhttp.cpp", "  bool pin(std::string pin, std::string name) {", "  template<class T>\n  void serverinfo", r'''  static bool awaiting_pin(pair_session_t &sess) {
    auto &response = sess.async_insert_pin.response;
    return sess.last_phase == PAIR_PHASE::NONE && sess.approval_token.size() == 64 &&
      ((response.has_left() && response.left()) || (response.has_right() && response.right()));
  }

  nlohmann::json pending_pairings() {
    std::lock_guard<std::recursive_mutex> lock {pairing_mutex};
    const auto now = std::chrono::steady_clock::now();
    prune_pairing_sessions(now);
    auto requests = nlohmann::json::array();
    if (!config::sunshine.enable_pairing) {
      return requests;
    }
    for (const auto &[id, sess] : map_id_sess) {
      if (awaiting_pin(*sess)) {
        requests.push_back({
          {"token", sess->approval_token},
          {"name", sess->client.name},
          {"source", sess->source},
          {"fingerprint", sess->certificate_fingerprint},
          {"expires_in", std::chrono::duration_cast<std::chrono::seconds>(PAIR_SESSION_TTL - (now - sess->created)).count()}
        });
      }
    }
    return requests;
  }

  bool pin(std::string pin, std::string name, std::string token) {
    std::lock_guard<std::recursive_mutex> lock {pairing_mutex};
    prune_pairing_sessions(std::chrono::steady_clock::now());
    if (!config::sunshine.enable_pairing || pin.size() != 4 || name.size() > 256 ||
        token.size() != 64 || !pairing_hex(token, 64) ||
        !std::all_of(pin.begin(), pin.end(), [](unsigned char c) { return std::isdigit(c); })) {
      return false;
    }

    std::shared_ptr<pair_session_t> sess;
    for (const auto &[id, candidate] : map_id_sess) {
      if (awaiting_pin(*candidate) &&
          CRYPTO_memcmp(candidate->approval_token.data(), token.data(), token.size()) == 0) {
        sess = candidate;
        break;
      }
    }
    if (!sess) {
      // Never fall back to a different request, even if only one remains.
      return false;
    }
    // Consume under the same lock before starting the handshake or sending data.
    sess->approval_token.clear();
    pt::ptree tree;
    getservercert(*sess, tree, pin);
    if (tree.get<int>("root.<xmlattr>.status_code") != 200) {
      return false;
    }
    if (!name.empty()) {
      sess->client.name = std::move(name);
    }
    std::ostringstream data;
    pt::write_xml(data, tree);
    auto &response = sess->async_insert_pin.response;
    if (response.has_left() && response.left()) {
      response.left()->write(data.str());
    } else if (response.has_right() && response.right()) {
      response.right()->write(data.str());
    }
    response = std::decay_t<decltype(response.left())>();
    return true;
  }

''')
replace("src/confighttp.cpp", "  void savePin(resp_https_t response, req_https_t request) {", r'''  void pendingPairings(resp_https_t response, req_https_t request) {
    if (!authenticate(response, request)) {
      return;
    }
    const nlohmann::json output {{"status", true}, {"requests", nvhttp::pending_pairings()}};
    const SimpleWeb::CaseInsensitiveMultimap headers {
      {"Content-Type", "application/json"},
      {"Cache-Control", "no-store"},
      {"Pragma", "no-cache"},
      {"X-Frame-Options", "DENY"},
      {"Content-Security-Policy", "frame-ancestors 'none';"}
    };
    // Device names are untrusted bytes; invalid UTF-8 must not break the API.
    response->write(output.dump(-1, ' ', false, nlohmann::json::error_handler_t::replace), headers);
  }

  void savePin(resp_https_t response, req_https_t request) {''')
replace("src/confighttp.cpp", "      output_tree[\"status\"] = nvhttp::pin(pin, name);", "      std::string token = input_tree.value(\"token\", \"\");\n      output_tree[\"status\"] = nvhttp::pin(pin, name, token);")
replace("src/confighttp.cpp", '   *   "name": "Friendly Client Name"', '   *   "name": "Friendly Client Name",\n   *   "token": "<server-generated token from /api/pairing/requests>"')
replace("src/confighttp.cpp", '   * @api_examples{/api/pin| POST| {"pin":"1234","name":"My PC"}}', '   * @api_examples{/api/pin| POST| {"pin":"1234","name":"My PC","token":"<request token>"}}')
replace("src/confighttp.cpp", '    server.resource["^/api/pin$"]["POST"] = savePin;', '    server.resource["^/api/pin$"]["POST"] = savePin;\n    server.resource["^/api/pairing/requests$"]["GET"] = pendingPairings;')

# Keep the existing page/OTP/device management; isolate the new approval UI.
replace("src_assets/common/assets/web/pin.html", "  import Checkbox from './Checkbox.vue'", "  import Checkbox from './Checkbox.vue'\n  import PairingApproval from './PairingApproval.vue'")
replace("src_assets/common/assets/web/pin.html", "      Navbar,\n      Checkbox\n", "      Navbar,\n      Checkbox,\n      PairingApproval\n")
section("src_assets/common/assets/web/pin.html", '    <form v-if="currentTab === \'#PIN\'"', '    <form v-else', '    <PairingApproval v-if="currentTab === \'#PIN\'" @submitted="pairingSubmitted" />\n')
section("src_assets/common/assets/web/pin.html", "      registerDevice(e) {", "      requestOTP() {", "      pairingSubmitted() {\n        setTimeout(() => this.refreshClients(), 1000);\n      },\n")
add("src_assets/common/assets/web/PairingApproval.vue", r'''<template>
  <form class="form w-100" @submit.prevent="submit">
    <div class="card p-4 mb-4">
      <h2>Approve a pairing request</h2>
      <p>Start pairing on your device, then refresh and select that exact request.
        Device names are supplied by the requester and are not verified. Check the source IP
        and certificate fingerprint; do not approve an unexpected or ambiguous request.</p>
      <button type="button" class="btn btn-secondary align-self-start mb-3"
              :disabled="loading || sending" @click="refresh">Refresh requests</button>
      <p v-if="loading">Loading pending requests...</p>
      <p v-else-if="!requests.length">No pending PIN requests. Start pairing on your device first.</p>
      <label v-for="request in requests" :key="request.token" class="border rounded p-3 mb-2">
        <input type="radio" name="pairing-request" :value="request.token" v-model="selectedToken"
               :disabled="sending" @change="resetPin" />
        <strong class="ms-2">{{ request.name || 'Unnamed device' }}</strong>
        <div>Source IP: <code>{{ request.source }}</code></div>
        <div>Certificate SHA-256: <code class="text-break">{{ request.fingerprint }}</code></div>
        <small>Expires in approximately {{ request.expires_in }} seconds from this refresh.</small>
      </label>
      <label for="pairing-pin" class="mt-3">PIN shown on the selected device</label>
      <input id="pairing-pin" class="form-control" v-model="pin" type="text" inputmode="numeric"
             pattern="[0-9]{4}" minlength="4" maxlength="4" autocomplete="off" required
             :disabled="!selectedToken || sending || loading" />
      <label for="pairing-name" class="mt-3">Optional device name</label>
      <input id="pairing-name" class="form-control mb-3" v-model="name" type="text" maxlength="256"
             :disabled="sending" />
      <button class="btn btn-primary" :disabled="!selectedToken || loading || sending">
        Submit PIN to selected request
      </button>
    </div>
    <p v-if="message" role="status" class="alert" :class="success ? 'alert-success' : 'alert-warning'">{{ message }}</p>
  </form>
</template>

<script>
export default {
  emits: ['submitted'],
  data() {
    return { requests: [], selectedToken: '', pin: '', name: '', loading: false,
      sending: false, message: '', success: false, controller: null, destroyed: false };
  },
  mounted() { this.refresh(); },
  beforeUnmount() {
    this.destroyed = true;
    this.controller?.abort();
    this.selectedToken = '';
    this.pin = '';
    this.requests = [];
  },
  methods: {
    resetPin() { this.pin = ''; this.message = ''; this.success = false; },
    async refresh() {
      if (this.sending || this.loading || this.destroyed) return;
      // A refresh never selects or silently retargets an approval.
      this.selectedToken = '';
      this.pin = '';
      this.requests = [];
      this.loading = true;
      this.controller = new AbortController();
      try {
        const response = await fetch('./api/pairing/requests', {
          credentials: 'include', cache: 'no-store', signal: this.controller.signal
        });
        if (!response.ok) throw new Error('Request list unavailable');
        const body = await response.json();
        if (body.status !== true || !Array.isArray(body.requests) || body.requests.some(request =>
          !request || typeof request.token !== 'string' || !/^[0-9a-fA-F]{64}$/.test(request.token) ||
          typeof request.name !== 'string' || typeof request.source !== 'string' ||
          typeof request.fingerprint !== 'string' || !Number.isFinite(request.expires_in))) {
          throw new Error('Invalid request list');
        }
        if (!this.destroyed) this.requests = body.requests;
      } catch (error) {
        if (!this.destroyed && error.name !== 'AbortError') {
          this.success = false;
          this.message = 'Could not load pending requests. Check your administrator session and refresh.';
        }
      } finally { this.loading = false; this.controller = null; }
    },
    async submit() {
      if (this.sending || this.loading || this.destroyed) return;
      const request = this.requests.find(item => item.token === this.selectedToken);
      if (!request || !/^[0-9]{4}$/.test(this.pin)) {
        this.success = false;
        this.message = 'Select a request and enter its four-digit PIN.';
        return;
      }
      // Snapshot the exact selection; never choose another request after failure.
      const payload = { token: request.token, pin: this.pin, name: this.name };
      this.sending = true;
      this.success = false;
      this.message = '';
      this.controller = new AbortController();
      try {
        const response = await fetch('./api/pin', {
          credentials: 'include', method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload), signal: this.controller.signal
        });
        if (!response.ok) throw new Error('Approval refused');
        const body = await response.json();
        if (body.status !== true) throw new Error('Request expired or already approved');
        if (!this.destroyed) {
          this.success = true;
          this.message = 'PIN submitted to the selected request. Complete pairing on your device and check its permissions.';
          this.$emit('submitted');
        }
      } catch (error) {
        if (!this.destroyed && error.name !== 'AbortError') {
          this.message = 'Approval failed or expired. Refresh and explicitly select a new request; no other request was approved automatically.';
        }
      } finally {
        this.sending = false;
        this.controller = null;
        this.selectedToken = '';
        this.pin = '';
        this.requests = [];
      }
    }
  }
};
</script>
''')

# Upgrade existing HTTP tests, keeping their real Moonlight-compatible handshakes.
path = "tests/unit/test_pairing_limits.cpp"
changes[path] = read(path).replace("nvhttp::pin(", "approve_single(")
replace(path, '"&phrase=getservercert&devicename=test&clientcert="', '"&phrase=getservercert&devicename=" + id + "&clientcert="')
replace(path, "    void wait_pending(std::size_t count) {", r'''    std::string approval_token(const std::string &id) {
      for (const auto &request : nvhttp::pending_pairings()) {
        if (request.at("name") == id) return request.at("token").get<std::string>();
      }
      ADD_FAILURE() << "Pending request not found: " << id;
      return {};
    }
    bool approve_single(const std::string &pin, const std::string &name) {
      auto requests = nvhttp::pending_pairings();
      if (requests.size() != 1) return false;
      return nvhttp::pin(pin, name, requests[0].at("token").get<std::string>());
    }
    void wait_pending(std::size_t count) {''')
section(path, "TEST_F(PairingHttp, PinCanBeCorrectedAndAdvancesOldestPendingRequest)", "TEST_F(PairingHttp, DuplicateIdCannotReplacePendingSaltOrResponse)", r'''TEST_F(PairingHttp, ApprovalTargetsSelectedRequestNotOldest) {
  auto first = begin(query("pin1")); wait_pending(1);
  auto second = begin(query("pin2")); wait_pending(2);
  const auto first_token = approval_token("pin1");
  const auto second_token = approval_token("pin2");
  ASSERT_EQ(first_token.size(), 64);
  ASSERT_EQ(second_token.size(), 64);
  EXPECT_NE(first_token, second_token);
  EXPECT_NE(first_token, "pin1");
  EXPECT_FALSE(nvhttp::pin("xx", "", second_token));
  ASSERT_TRUE(nvhttp::pin("5338", "second", second_token));
  const auto body = read(second);
  EXPECT_NE(body.find("paired>1"), std::string::npos);
  EXPECT_EQ(body.find(second_token), std::string::npos);
  EXPECT_FALSE(nvhttp::pin("5338", "", second_token));
  ASSERT_EQ(nvhttp::pending_pairings().size(), 1);
  EXPECT_EQ(approval_token("pin1"), first_token);
  ASSERT_TRUE(nvhttp::pin("5338", "first", first_token));
  EXPECT_NE(read(first).find("paired>1"), std::string::npos);
  EXPECT_TRUE(nvhttp::pending_pairings().empty());
}
''')
changes[path] = read(path) + r'''

TEST_F(PairingHttp, MissingUnknownAndClientSuppliedTokensCannotApprove) {
  auto request = begin(query("untrusted-id")); wait_pending(1);
  auto token = approval_token("untrusted-id");
  EXPECT_FALSE(nvhttp::pin("5338", "", ""));
  EXPECT_FALSE(nvhttp::pin("5338", "", "untrusted-id"));
  EXPECT_FALSE(nvhttp::pin("5338", "", std::string(64, 'z')));
  auto wrong = token; wrong[0] = wrong[0] == '0' ? '1' : '0';
  EXPECT_FALSE(nvhttp::pin("5338", "", wrong));
  ASSERT_EQ(nvhttp::pending_pairings().size(), 1);
  ASSERT_TRUE(nvhttp::pin("5338", "", token));
  read(request);
}
TEST_F(PairingHttp, ExpiredTokenCannotApproveReusedClientId) {
  auto old_request = begin(query("reused")); wait_pending(1);
  auto old_token = approval_token("reused");
  nvhttp::test::expire(); read(old_request);
  auto replacement = begin(query("reused")); wait_pending(1);
  auto new_token = approval_token("reused");
  EXPECT_NE(old_token, new_token);
  EXPECT_FALSE(nvhttp::pin("5338", "", old_token));
  ASSERT_TRUE(nvhttp::pin("5338", "", new_token));
  read(replacement);
}
TEST_F(PairingHttp, OlderAttackerRequestsDoNotReceiveLegitimateApproval) {
  begin(query("attacker1"), 2); wait_pending(1);
  begin(query("attacker2"), 2); wait_pending(2);
  auto legitimate = begin(query("legitimate")); wait_pending(3);
  const auto token = approval_token("legitimate");
  ASSERT_TRUE(nvhttp::pin("5338", "legitimate", token));
  read(legitimate);
  ASSERT_TRUE(finish_pair("legitimate", "5338"));
  ASSERT_EQ(nvhttp::get_all_clients().size(), 1);
  EXPECT_EQ(nvhttp::get_all_clients()[0]["name"], "legitimate");
  ASSERT_EQ(nvhttp::pending_pairings().size(), 2);
  EXPECT_FALSE(nvhttp::pin("5338", "", token));
}
TEST_F(PairingHttp, ConcurrentApprovalConsumesTokenExactlyOnce) {
  auto request = begin(query("concurrent-approval")); wait_pending(1);
  const auto token = approval_token("concurrent-approval");
  auto first = std::async(std::launch::async, [&] { return nvhttp::pin("5338", "", token); });
  auto second = std::async(std::launch::async, [&] { return nvhttp::pin("5338", "", token); });
  const bool a = first.get(), b = second.get();
  ASSERT_NE(a, b);
  read(request);
  ASSERT_TRUE(finish_pair("concurrent-approval", "5338"));
}
TEST_F(PairingHttp, DisabledPairingRejectsExistingApprovalTokens) {
  auto request = begin(query("disabled-existing")); wait_pending(1);
  const auto token = approval_token("disabled-existing");
  config::sunshine.enable_pairing = false;
  EXPECT_TRUE(nvhttp::pending_pairings().empty());
  EXPECT_FALSE(nvhttp::pin("5338", "", token));
  config::sunshine.enable_pairing = true;
  ASSERT_TRUE(nvhttp::pin("5338", "", token));
  read(request);
}
'''

# The immutable hotfix overlay deliberately lacks the new approval API. Link
# test-only fail-closed adapters in that disposable source, NEVER in production.
replace("ci/baseline-regressions.py", 'namespace nvhttp::test {\n  std::recursive_mutex harness_mutex;', '''namespace nvhttp {
  nlohmann::json pending_pairings() { return nlohmann::json::array(); }
  bool pin(std::string, std::string, std::string) { return false; }
}
namespace nvhttp::test {
  std::recursive_mutex harness_mutex;''')

# Add an executable UI regression suite, without introducing npm dependencies.
add("tests/web/pairing-approval.test.mjs", r'''import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
const source = readFileSync('src_assets/common/assets/web/PairingApproval.vue', 'utf8');
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1];
const component = (await import('data:text/javascript;base64,' + Buffer.from(script).toString('base64'))).default;
function instance() {
  const context = { ...component.data(), events: [], $emit(event) { this.events.push(event); } };
  for (const [name, method] of Object.entries(component.methods)) context[name] = method.bind(context);
  return context;
}
function request(char) {
  return { token: char.repeat(64), name: 'Same untrusted name', source: '192.0.2.1',
    fingerprint: '00'.repeat(32), expires_in: 120 };
}
test('refresh does not automatically select even a single request', async () => {
  const context = instance();
  context.selectedToken = 'old'; context.pin = '5338';
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, './api/pairing/requests');
      assert.equal(options.credentials, 'include'); assert.equal(options.cache, 'no-store');
      return { ok: true, json: async () => ({ status: true, requests: [request('a')] }) };
    };
    await context.refresh();
    assert.equal(context.requests.length, 1);
    assert.equal(context.selectedToken, ''); assert.equal(context.pin, '');
  } finally { globalThis.fetch = original; }
});
test('submission uses the selected token, not the first or matching device name', async () => {
  const context = instance(); context.requests = [request('a'), request('b')];
  context.selectedToken = request('b').token; context.pin = '5338'; context.name = 'trusted';
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, './api/pin'); assert.equal(options.method, 'POST');
      assert.deepEqual(JSON.parse(options.body), { token: 'b'.repeat(64), pin: '5338', name: 'trusted' });
      return { ok: true, json: async () => ({ status: true }) };
    };
    await context.submit(); assert.equal(context.success, true);
    assert.deepEqual(context.events, ['submitted']);
    assert.equal(context.selectedToken, ''); assert.equal(context.pin, '');
  } finally { globalThis.fetch = original; }
});
test('stale selection cannot submit or fall back to another request', async () => {
  const context = instance(); context.requests = [request('a')];
  context.selectedToken = request('b').token; context.pin = '5338';
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => { assert.fail('A stale selection must not send any approval'); };
    await context.submit(); assert.equal(context.success, false);
  } finally { globalThis.fetch = original; }
});
test('refused approval is never retried or retargeted automatically', async () => {
  const context = instance(); context.requests = [request('a'), request('b')];
  context.selectedToken = request('a').token; context.pin = '5338';
  const original = globalThis.fetch; let calls = 0;
  try {
    globalThis.fetch = async () => { calls++; return { ok: true, json: async () => ({ status: false }) }; };
    await context.submit(); assert.equal(calls, 1); assert.equal(context.success, false);
    assert.equal(context.selectedToken, ''); assert.equal(context.requests.length, 0);
    assert.equal(context.events.length, 0);
  } finally { globalThis.fetch = original; }
});
''')

notes = """## Apollo 0.4.7-beta.3 — targeted network security fixes

- Keep sender endpoints separate for concurrent video/audio UDP receives and snapshot
  the correct sender with each received packet.
- Replace implicit oldest-request PIN approval with explicit, administrator-selected
  requests identified by cryptographically random, single-use 256-bit server tokens.
  Requests display their observed source IP and SHA-256 certificate fingerprint;
  requester-provided device names are explicitly untrusted. Expired/replayed/missing
  tokens fail closed and never approve another pending request.
- The authenticated management API now requires `token` in `POST /api/pin`;
  obtain pending approvals via authenticated `GET /api/pairing/requests` (no-store).
  Update external management scripts using the old two-field PIN API.
- Existing paired clients, GameStream/Moonlight wire protocol, OTP pairing, codecs,
  dependency versions and pinned submodules are unchanged. The legacy four-digit PIN
  exchange has not been redesigned; administrators must verify the selected request.
- Regression coverage exercises real UDP receives, concurrent/replayed/expired approvals,
  complete PIN/OTP handshakes and the Web UI's explicit selection behavior. Publication
  remains gated on the Windows validation/build/installer pipeline. Hardware streaming
  and interactive installation are not replaced by automated headless tests.

---

"""
changes["docs/release-0.4.7.md"] = notes + read("docs/release-0.4.7.md")

# Write only the reviewed candidate files. Release workflow changes are committed
# separately through the user's GitHub connection (not an Actions token).
for path, text in changes.items():
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8", newline="\n")
subprocess.run(["git", "diff", "--check"], check=True)
print("Prepared candidate files:\n" + "\n".join(sorted(changes)))
