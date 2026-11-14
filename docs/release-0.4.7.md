- Streaming now requires authenticated, encrypted RTSP, with protection against replayed requests and expired launches.
- Added explicit pairing-request selection with source and certificate details, backed by single-use approval tokens.
- Strengthened validation of pairing requests, device names and stream parameters, and limited pending requests and idle connections.
- Fixed stream cleanup hangs and shutdown races, and separated audio and video receive handling.
- Corrected Windows service path quoting and handling of spaces, ampersands and exclamation marks.

**Compatibility:** Existing pairings remain supported. Upgrade clients that lack encrypted RTSP support. Custom pairing integrations must obtain a token from authenticated `GET /api/pairing/requests` and include it in `POST /api/pin`.
