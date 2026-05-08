"""Wrap-aware monotonicity helpers for cmd_seq tests.

Tests that assert a stream of CMD frames advances must use these helpers
instead of `seqs[i] > seqs[i - 1]`. The strict `>` form was the audit-flagged
hazard that prevented test_reserve_cmd_seq_wraps_after_int32_maximum from
being internally consistent with the e2e suites — both code paths can now
produce a `MAX -> 0` transition that strict `>` would falsely flag as a
regression.

Mirrors `_seq_advances` in src/backend/state.py and fc::cmd::seq_advances in
src/fc/header/CmdSeq.h. All three implementations MUST agree on every
boundary case.
"""

from __future__ import annotations

from src.backend.state import CMD_SEQ_MAX

_CMD_SEQ_MOD = CMD_SEQ_MAX + 1
_CMD_SEQ_HALF = _CMD_SEQ_MOD // 2


def seq_advances(prev: int, candidate: int) -> bool:
    """Return True iff `candidate` is strictly ahead of `prev` in cmd_seq's
    wrapping space.

    Equal seqs => False (dedup). Distances larger than half the space are
    treated as "behind" (a backward jump, not a tiny forward one across the
    boundary).
    """
    diff = (candidate - prev) % _CMD_SEQ_MOD
    return 0 < diff <= _CMD_SEQ_HALF


def assert_seq_advances(prev: int, candidate: int) -> None:
    """assert helper: fails with a wrap-aware diagnostic on a non-advancing pair."""
    if not seq_advances(prev, candidate):
        raise AssertionError(
            f"cmd_seq did not advance: prev={prev}, candidate={candidate} "
            f"(modular forward distance ∉ (0, {_CMD_SEQ_HALF}])"
        )


def assert_seqs_strictly_advance(seqs: list[int]) -> None:
    """assert each consecutive pair in `seqs` advances per signed-modular semantics."""
    for idx in range(1, len(seqs)):
        assert_seq_advances(seqs[idx - 1], seqs[idx])
