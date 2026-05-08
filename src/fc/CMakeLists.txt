set(FC_HEADER_DIR ${CMAKE_CURRENT_SOURCE_DIR}/header)
set(FC_IMPL_DIR ${CMAKE_CURRENT_SOURCE_DIR}/implementation)

add_library(fc STATIC
    ${FC_IMPL_DIR}/FrameCodec.cpp
    ${FC_IMPL_DIR}/CommandReceiver.cpp
    ${FC_IMPL_DIR}/CommandServer.cpp
    ${FC_IMPL_DIR}/TelemetryPublisher.cpp
    ${FC_IMPL_DIR}/FlightController.cpp
    ${FC_IMPL_DIR}/RcSink.cpp
)

target_include_directories(fc PUBLIC ${FC_HEADER_DIR})

add_executable(fc_app
    ${FC_IMPL_DIR}/main.cpp
)
target_link_libraries(fc_app PRIVATE fc)
