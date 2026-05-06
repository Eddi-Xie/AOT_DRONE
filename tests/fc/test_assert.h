#pragma once

// Always-on test assertion macro for the C++ unit tests under tests/fc/.
//
// We deliberately avoid <cassert> / assert() because that macro is a no-op
// under -DNDEBUG. Many CI setups and Release builds define NDEBUG, which
// would let a broken mapping/clamp helper silently report ctest success
// even though the test body never actually checks anything. This macro
// always fires on failure regardless of build type.
//
// Prints file:line and the failed expression to stderr, then aborts so
// ctest sees a non-zero exit code.

#include <cstdio>
#include <cstdlib>

#define TEST_ASSERT(cond)                                                                          \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            std::fprintf(stderr, "%s:%d: TEST_ASSERT failed: %s\n", __FILE__, __LINE__, #cond);    \
            std::abort();                                                                          \
        }                                                                                          \
    } while (0)
