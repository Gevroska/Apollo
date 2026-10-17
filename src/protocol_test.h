#pragma once
#ifdef SUNSHINE_TESTS
#include "nvhttp.h"
#include "rtsp.h"
namespace rtsp_stream::test {
  enum class retirement { none, clear, expire, replace, wrong_id, terminate };
  struct result {
    std::vector<std::string> responses;
    std::vector<std::string> commands;
    uint32_t response_counter;
    bool pending;
  };
  result exchange(std::shared_ptr<launch_session_t> session, const std::vector<std::string> &messages,
                  retirement retire = retirement::none, bool production_handlers = false);
  bool stalled_sockets_close(bool expire, bool partial_header);
  bool socket_capacity_recovers();
  bool control_retirement_does_not_block_cleanup();
}
namespace nvhttp::test {
  void pair_http(std::shared_ptr<SimpleWeb::ServerBase<SimpleWeb::HTTP>::Response> response,
                 std::shared_ptr<SimpleWeb::ServerBase<SimpleWeb::HTTP>::Request> request);
  std::size_t pending();
  std::size_t rate_sources();
  void reset();
  void expire();
}
namespace stream::test {
  bool control_session_lock_order(bool correct_connect_data);
  bool media_endpoint_isolation(bool legacy);
  bool media_receive_shutdown(bool before_start);
}
#endif
