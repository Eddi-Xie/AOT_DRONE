#pragma once

// Signed-modular comparator for cmd.seq, the per-CMD-frame counter the backend
// emits and the FC dedupes against. seq lives in [0, CMD_SEQ_MAX] where
// CMD_SEQ_MAX = 2^31 - 1 (signed int32 max), wrapping at CMD_SEQ_MAX -> 0.
//
// We compare candidates against the last-seen seq with signed-modular
// semantics so wrap is handled correctly: at the wrap point a candidate of 0
// after a last-seen of CMD_SEQ_MAX is "ahead by one", not "behind by
// 2,147,483,647". The simple `(int32_t)(b - a)` trick that works for full
// uint32 wrap does NOT apply here because our space is half that size — the
// boundary between "ahead" and "behind" is 2^30, not 0x80000000. The mask +
// half-space comparison below handles it.
//
// Mirrors `_seq_advances` in src/backend/state.py — both sides MUST use the
// same semantics or wrap-state restarts will silently re-issue old seqs.

#include <cstdint>

namespace fc::cmd {

inline constexpr std::uint32_t kCmdSeqMod = 0x80000000u;  // 2^31 (CMD_SEQ_MAX + 1)
inline constexpr std::uint32_t kCmdSeqHalf = 0x40000000u; // 2^30

// Returns true iff `candidate` is strictly ahead of `prev` in the wrapping
// seq space. Equal values return false (dedup). Distances larger than half
// the space are treated as "behind" (i.e. a backward jump rather than a tiny
// forward one across the wrap).
inline bool seq_advances(std::int32_t prev, std::int32_t candidate) {
    // Both inputs are in [0, 0x7FFFFFFF]; their int32 difference is in
    // [-0x7FFFFFFF, 0x7FFFFFFF] — no overflow. Reinterpret as uint32 (well-
    // defined modular cast) and mask to 31 bits, then compare against the
    // half-space threshold.
    const std::uint32_t diff = static_cast<std::uint32_t>(candidate - prev) & (kCmdSeqMod - 1u);
    return diff > 0u && diff <= kCmdSeqHalf;
}

} // namespace fc::cmd
