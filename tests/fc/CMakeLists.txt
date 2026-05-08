# C++ unit tests for src/fc/. Each test is a tiny self-contained
# executable using the always-on TEST_ASSERT macro from
# tests/fc/test_assert.h (we deliberately avoid <cassert> because
# assert() is compiled out under -DNDEBUG, which would let broken logic
# silently report ctest success in Release builds). Run via `ctest` after
# `cmake --build`.

add_executable(test_rc_math test_rc_math.cpp)
target_link_libraries(test_rc_math PRIVATE fc)
add_test(NAME test_rc_math COMMAND test_rc_math)

add_executable(test_clamp test_clamp.cpp)
target_link_libraries(test_clamp PRIVATE fc)
add_test(NAME test_clamp COMMAND test_clamp)

add_executable(test_cmd_seq_wrap test_cmd_seq_wrap.cpp)
target_link_libraries(test_cmd_seq_wrap PRIVATE fc)
add_test(NAME test_cmd_seq_wrap COMMAND test_cmd_seq_wrap)

add_executable(test_command_server_atomic test_command_server_atomic.cpp)
target_link_libraries(test_command_server_atomic PRIVATE fc)
add_test(NAME test_command_server_atomic COMMAND test_command_server_atomic)
