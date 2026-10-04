Apollo 0.4.7 is based on the approved Windows hotfix source `8d789be4`, without the unrelated changes on master or changes to pinned submodules.

This release requires authenticated encrypted RTSP for client streaming launches and resumes. Recent Artemis Android and Moonlight PC clients support this protocol. Legacy clients without encrypted RTSP must be upgraded. Invalid frames cannot alter the legitimate launch's encryption counters; replayed requests and sockets retained after expiry or cancellation cannot start another session.

Pairing now has a global limit of 32 pending attempts, four per source, a bounded rate limiter, strict field bounds, and a 180-second absolute lifetime with periodic cleanup. Invalid OTP requests retain no pending state. PIN and OTP handshakes remain supported.

Windows validation gates run real loopback protocol handlers, repeated security regressions, existing headless unit tests, and four sensitivity checks against the original vulnerable handlers. The distribution build is separate and contains neither the test harness nor coverage instrumentation. The installer is checked for version 0.4.7 and the original hash-pinned SudoVDA driver files.

The beta is a prerelease for the owner's validation with Artemis Android and Moonlight Windows/Fedora. Audio/video smoothness, hardware encoding, virtual-display operation and input must still be tried on the owner's devices before promotion to v0.4.7. This installer is unsigned. SHA256SUMS.txt identifies the installer and the stripped executable extracted from it. Build commit, submodule revisions and dependency manifests are included.

Stable v0.4.7 publication additionally requires the Windows dependency manifest to match v0.4.7-beta.1 and reuses that beta's verified npm lock. A changed toolchain requires a new beta and revalidation before publication.

The inherited headless suite is also executed against the immutable hotfix. Publication requires all 31 new tests to pass and no new failure among the existing tests. The 31 existing failures in the old mDNS/display expectations are recorded explicitly and must reproduce on both versions; their production source files must remain unchanged. XML reports and the comparison summary are retained.

All 211 existing tests run on the patched candidate; 202 unchanged tests also run on the old source. The nine existing pairing helper fixtures run only on the candidate because the old helper erases an absent map iterator and crashes when these standalone fixtures complete. The new real HTTP pairing tests additionally verify complete PIN and OTP exchanges through production handlers.
