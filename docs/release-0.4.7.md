# Apollo 0.4.7-beta.4

This corrective prerelease reviews the complete beta.1, beta.2 and beta.3 changes against approved hotfix `8d789be463ef72cadb1ba16e202896b11ea7664b`.

- Close idle and partially received RTSP connections when their launch expires or is cancelled, and cap outstanding handshake sockets at 64.
- Retire RTSP launches without blocking the control thread, removing a three-thread cleanup/control/RTSP deadlock while preserving stream teardown serialization. Cancel UDP receives on their executor and drain their completions before releasing receive buffers or closing shared sockets.
- Validate real RTSP SETUP targets and ANNOUNCE attributes, numeric conversion and unsafe stream parameters before allocating streaming resources. Keep integer and fractional frame-rate requests supported.
- Reject incomplete pairing challenges and responses immediately, check cryptographic decryption results, and reject invalid UTF-8 device names before they can enter persistent client state.
- Preserve literal exclamation marks in Windows installation and profile paths. CI now executes the complete packaged service script for creation and repair, with spaces, parentheses, ampersands, exclamation marks, percent signs and both LF/CRLF line endings.
- Require the exact 51-test protocol/control inventory and reject skipped, disabled, incomplete or duplicate XML test results. Repeat all 51 checks five times; retain the immutable hotfix comparison and four regression sensitivity checks.

The changes retain the earlier protections: authenticated encrypted RTSP for streaming launches/resumes; replay and retired-launch rejection; isolated audio/video receive endpoints; explicit administrator-selected PIN approvals bound to single-use server tokens; bounded pairing state and rate tracking; and quoted Windows service executable paths.

The authenticated management API requires `token` in `POST /api/pin`, obtained from authenticated `GET /api/pairing/requests`. Device names remain requester-supplied and unverified; administrators must verify the selected request's source and certificate fingerprint. Existing paired clients and complete PIN/OTP exchanges remain supported. Clients without encrypted RTSP support must be upgraded.

Direct dependency declarations and pinned submodule revisions remain unchanged. Beta.4 reuses beta.3's published, checksum-verified npm lock. The Windows toolchain package manifest is recorded for this build; stable v0.4.7 must match beta.4's manifest and npm lock.

Publication requires 262 executed candidate tests: 231 passes and the same 31 explicitly recorded mDNS/display expectation failures reproduced by the 202-test immutable hotfix baseline. The nine repaired pairing helper fixtures run only on the candidate; malformed inputs now fail at the correct earlier phase. The 51 protocol/control checks include real RTSP handlers, complete pairing exchanges, concurrency, socket expiry/capacity and UDP cancellation. Separate Web UI and release-gate tests also run. No new failure or skipped test is permitted.

Windows CI builds the distribution separately without test instrumentation, packages the NSIS installer, verifies its service script, numeric/product version and original hash-pinned SudoVDA driver files, and publishes SHA-256 checksums plus build/dependency metadata.

This installer is unsigned and remains a prerelease for validation with Artemis Android and Moonlight Windows/Fedora. Installation, actual service startup, audio/video smoothness, hardware encoding, virtual displays and input still require checks on the owner's devices before stable promotion. Automated headless results do not establish hardware streaming acceptance.
