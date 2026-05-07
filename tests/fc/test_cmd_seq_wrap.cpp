// Unit tests for fc::cmd::seq_advances — the signed-modular int32 comparator
// the FC main loop uses to dedupe and order incoming CMD frames.
//
// The comparator MUST agree with src/backend/state.py::_seq_advances on every
// boundary case; if they drift, the FC and backend will disagree at wrap and
// the FC will silently drop or re-accept stale CMDs. The tests below pin the
// contract: forward = accept, dedup = reject, backward = reject, wrap forward
// = accept, wrap backward = reject, half-space boundary handled.
//
// Uses the always-on TEST_ASSERT macro from tests/fc/test_assert.h instead of
// <cassert>'s assert(), because assert() is a no-op under -DNDEBUG.
// Run via `ctest --test-dir build --output-on-failure`.

#include "CmdSeq.h"
#include "test_assert.h"

#include <cstdint>
#include <cstdio>

namespace {

// Mirrors backend's CMD_SEQ_MAX (state.py:8).
constexpr std::int32_t kCmdSeqMax = 0x7FFFFFFF;
constexpr std::int32_t kHalf = 0x40000000; // 2^30

void test_forward_simple_advances() {
    using fc::cmd::seq_advances;
    TEST_ASSERT(seq_advances(0, 1));
    TEST_ASSERT(seq_advances(100, 101));
    TEST_ASSERT(seq_advances(100, 1000));
}

void test_dedup_rejects_same_seq() {
    using fc::cmd::seq_advances;
    TEST_ASSERT(!seq_advances(0, 0));
    TEST_ASSERT(!seq_advances(100, 100));
    TEST_ASSERT(!seq_advances(kCmdSeqMax, kCmdSeqMax));
}

void test_backward_simple_rejects() {
    using fc::cmd::seq_advances;
    TEST_ASSERT(!seq_advances(101, 100));
    TEST_ASSERT(!seq_advances(1000, 999));
    TEST_ASSERT(!seq_advances(1000, 0));
}

void test_wrap_forward_accepts() {
    using fc::cmd::seq_advances;
    // The classic wrap case: backend wraps from CMD_SEQ_MAX -> 0. The next
    // valid frame's seq is 0 — must be treated as "ahead by 1" relative to
    // CMD_SEQ_MAX, not "behind by 2^31 - 1".
    TEST_ASSERT(seq_advances(kCmdSeqMax, 0));
    TEST_ASSERT(seq_advances(kCmdSeqMax, 1));
    TEST_ASSERT(seq_advances(kCmdSeqMax - 1, kCmdSeqMax));
    TEST_ASSERT(seq_advances(kCmdSeqMax - 1, 0)); // forward 2 across wrap
}

void test_wrap_backward_rejects() {
    using fc::cmd::seq_advances;
    // The backward wrap: prev=0, candidate=CMD_SEQ_MAX would mean a forward
    // jump of 2^31 - 1 (≈ half-space minus 1) but the comparator must
    // resolve large jumps as "behind", protecting against a stale frame from
    // before a backend reseed.
    TEST_ASSERT(!seq_advances(0, kCmdSeqMax));
    TEST_ASSERT(!seq_advances(1, kCmdSeqMax));
    TEST_ASSERT(!seq_advances(0, kCmdSeqMax - 1));
}

void test_half_space_boundary() {
    using fc::cmd::seq_advances;
    // diff == 2^30 is the threshold: still "ahead" by convention.
    TEST_ASSERT(seq_advances(0, kHalf));
    // diff == 2^30 + 1 is "behind" (closer in the reverse direction).
    TEST_ASSERT(!seq_advances(0, kHalf + 1));
}

void test_first_frame_after_sentinel_clear() {
    using fc::cmd::seq_advances;
    // Sanity: the FC main loop guards `seq_advances` with
    // `!last_cmd_seq.has_value()` so the first frame is always accepted
    // regardless of seq value. The comparator itself never sees the
    // sentinel — but for completeness, comparing a tiny seq against a
    // freshly-reseeded one (e.g. backend just restarted with a small
    // initial seq) should advance from any prior small seq.
    TEST_ASSERT(seq_advances(5, 42));
    TEST_ASSERT(seq_advances(0, 1));
}

} // namespace

int main() {
    test_forward_simple_advances();
    test_dedup_rejects_same_seq();
    test_backward_simple_rejects();
    test_wrap_forward_accepts();
    test_wrap_backward_rejects();
    test_half_space_boundary();
    test_first_frame_after_sentinel_clear();
    std::printf("test_cmd_seq_wrap: 7 cases passed\n");
    return 0;
}
