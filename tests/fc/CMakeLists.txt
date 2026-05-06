# C++ unit tests for src/fc/. Each test is a tiny self-contained executable
# using <cassert>; run via `ctest` after `cmake --build`.

add_executable(test_rc_math test_rc_math.cpp)
target_link_libraries(test_rc_math PRIVATE fc)
add_test(NAME test_rc_math COMMAND test_rc_math)

add_executable(test_clamp test_clamp.cpp)
target_link_libraries(test_clamp PRIVATE fc)
add_test(NAME test_clamp COMMAND test_clamp)
