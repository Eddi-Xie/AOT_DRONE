add_library(fc STATIC
    placeholder.cpp
    FrameCodec.cpp
    CommandServer.cpp
)

target_include_directories(fc PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
