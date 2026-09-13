import shutil
import subprocess
from pathlib import Path

import pytest


def _compile_and_run_cpp(
    tmp_path: Path, source_text: str, implementation_files: list[Path]
) -> subprocess.CompletedProcess[str]:
    cxx = shutil.which("c++")
    if cxx is None:
        pytest.skip("c++ compiler not available")

    repo_root = Path(__file__).resolve().parents[1]
    header_dir = repo_root / "src" / "fc" / "header"

    source_file = tmp_path / "fc_tracking_test.cpp"
    binary_file = tmp_path / "fc_tracking_test_bin"
    source_file.write_text(source_text, encoding="utf-8")

    compile_cmd = [
        cxx,
        "-std=c++17",
        "-pthread",
        "-I",
        str(header_dir),
        *(str(path) for path in implementation_files),
        str(source_file),
        "-o",
        str(binary_file),
    ]
    compile_result = subprocess.run(compile_cmd, capture_output=True, text=True, check=False)
    if compile_result.returncode != 0:
        pytest.fail(
            "C++ compile failed:\n"
            f"stdout:\n{compile_result.stdout}\n"
            f"stderr:\n{compile_result.stderr}"
        )

    return subprocess.run([str(binary_file)], capture_output=True, text=True, check=False)


def test_parse_cmd_frame_extracts_tracking_object(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    impl = repo_root / "src" / "fc" / "implementation" / "CommandReceiver.cpp"

    source = r"""
#include "CommandReceiver.h"

#include <cmath>
#include <string>

int main() {
    const std::string json =
        R"({"type":"CMD","seq":10,"timestamp_s":42.0,"desired_mode":1,"tracking":{"tracking_state":3,"loc_x":0.25,"loc_y":-0.1,"bound_w":0.2,"bound_h":0.3,"confidence":0.88,"vis_seq":55,"vis_timestamp_s":10.2}})";

    fc::CommandFrame frame;
    if (!fc::parse_cmd_frame(json, frame)) {
        return 1;
    }
    if (!frame.tracking.has_tracking) {
        return 2;
    }
    if (frame.tracking.tracking_state != 3) {
        return 3;
    }
    if (std::fabs(frame.tracking.loc_x - 0.25) > 1e-9) {
        return 4;
    }
    if (frame.tracking.vis_seq != 55) {
        return 5;
    }
    if (std::fabs(frame.tracking.vis_timestamp_s - 10.2) > 1e-9) {
        return 6;
    }

    return 0;
}
"""
    run_result = _compile_and_run_cpp(tmp_path, source, [impl])
    assert run_result.returncode == 0, run_result.stderr + run_result.stdout


def test_parse_cmd_frame_scopes_setpoints_to_setpoints_object(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    impl = repo_root / "src" / "fc" / "implementation" / "CommandReceiver.cpp"

    source = r"""
#include "CommandReceiver.h"

#include <cmath>
#include <string>

int main() {
    const std::string json =
        R"({"type":"CMD","seq":12,"timestamp_s":44.0,"desired_mode":0,"roll":0.9,"throttle":0.7,"meta":{"pitch":0.8,"yaw_rate":0.2},"setpoints":{"roll":0.1,"pitch":-0.2}})";

    fc::CommandFrame frame;
    if (!fc::parse_cmd_frame(json, frame)) {
        return 1;
    }
    if (!frame.setpoints.has_roll || std::fabs(frame.setpoints.roll - 0.1) > 1e-9) {
        return 2;
    }
    if (!frame.setpoints.has_pitch || std::fabs(frame.setpoints.pitch + 0.2) > 1e-9) {
        return 3;
    }
    if (frame.setpoints.has_yaw_rate) {
        return 4;
    }
    if (frame.setpoints.has_throttle) {
        return 5;
    }

    return 0;
}
"""
    run_result = _compile_and_run_cpp(tmp_path, source, [impl])
    assert run_result.returncode == 0, run_result.stderr + run_result.stdout


def test_parse_cmd_frame_scopes_tracking_to_tracking_object(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    impl = repo_root / "src" / "fc" / "implementation" / "CommandReceiver.cpp"

    source = r"""
#include "CommandReceiver.h"

#include <cmath>
#include <string>

int main() {
    const std::string json =
        R"({"type":"CMD","seq":13,"timestamp_s":45.0,"desired_mode":1,"decoy":{"tracking_state":1,"loc_x":-0.9,"loc_y":-0.9,"bound_w":0.01,"bound_h":0.01,"confidence":0.01,"vis_seq":1,"vis_timestamp_s":1.0},"tracking":{"tracking_state":3,"loc_x":0.2,"loc_y":0.1,"bound_w":0.2,"bound_h":0.3,"confidence":0.9,"vis_seq":99,"vis_timestamp_s":55.0}})";

    fc::CommandFrame frame;
    if (!fc::parse_cmd_frame(json, frame)) {
        return 1;
    }
    if (!frame.tracking.has_tracking) {
        return 2;
    }
    if (frame.tracking.tracking_state != 3) {
        return 3;
    }
    if (std::fabs(frame.tracking.loc_x - 0.2) > 1e-9) {
        return 4;
    }
    if (frame.tracking.vis_seq != 99) {
        return 5;
    }

    return 0;
}
"""
    run_result = _compile_and_run_cpp(tmp_path, source, [impl])
    assert run_result.returncode == 0, run_result.stderr + run_result.stdout


def test_parse_cmd_frame_rejects_non_object_tracking_field(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    impl = repo_root / "src" / "fc" / "implementation" / "CommandReceiver.cpp"

    source = r"""
#include "CommandReceiver.h"

#include <string>

int main() {
    const std::string json =
        R"({"type":"CMD","seq":14,"timestamp_s":46.0,"desired_mode":1,"tracking":3})";

    fc::CommandFrame frame;
    if (fc::parse_cmd_frame(json, frame)) {
        return 1;
    }
    return 0;
}
"""
    run_result = _compile_and_run_cpp(tmp_path, source, [impl])
    assert run_result.returncode == 0, run_result.stderr + run_result.stdout


def test_tracking_payload_can_drive_controller_tracking_state(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    command_receiver_impl = repo_root / "src" / "fc" / "implementation" / "CommandReceiver.cpp"
    flight_controller_impl = repo_root / "src" / "fc" / "implementation" / "FlightController.cpp"

    source = r"""
#include "CommandReceiver.h"
#include "FlightController.h"

namespace {
bool to_tracking_state(int raw_state, fc::TrackingState& out_state) {
    switch (raw_state) {
    case 1:
        out_state = fc::TrackingState::NoTarget;
        return true;
    case 2:
        out_state = fc::TrackingState::TargetDetected;
        return true;
    case 3:
        out_state = fc::TrackingState::Tracking;
        return true;
    case 4:
        out_state = fc::TrackingState::Searching;
        return true;
    default:
        return false;
    }
}
} // namespace

int main() {
    const std::string json =
        R"({"type":"CMD","seq":11,"timestamp_s":43.0,"desired_mode":1,"tracking":{"tracking_state":3,"loc_x":0.3,"loc_y":0.0,"bound_w":0.2,"bound_h":0.1,"confidence":0.95,"vis_seq":56,"vis_timestamp_s":11.2}})";

    fc::CommandFrame frame;
    if (!fc::parse_cmd_frame(json, frame)) {
        return 1;
    }
    if (!frame.tracking.has_tracking) {
        return 2;
    }

    fc::TrackingState state = fc::TrackingState::Searching;
    if (!to_tracking_state(frame.tracking.tracking_state, state)) {
        return 3;
    }

    fc::FlightController controller;
    // S0.9 per-tick uncalibrated-hover guard routes Tracking → LandSafely
    // when hoverThrottle_ == 0. Calibrate explicitly for this test.
    controller.setHoverThrottle(1100);
    controller.setControlMode(fc::ControlMode::Tracking);

    fc::TrackingMessage tracking{};
    tracking.state = state;
    tracking.target_x = frame.tracking.loc_x;
    tracking.target_y = frame.tracking.loc_y;
    tracking.bound_w = frame.tracking.bound_w;
    tracking.bound_h = frame.tracking.bound_h;
    tracking.confidence = frame.tracking.confidence;
    tracking.timestamp_s = frame.tracking.vis_timestamp_s;
    controller.updateTracking(tracking);

    const fc::BetaFlightCommand out = controller.updateTimeStep(0.02);
    const fc::TelemetryData& tel = controller.getTelemetryData();

    if (tel.tracking_state != fc::TrackingState::Tracking) {
        return 4;
    }
    if (tel.confidence <= 0.0) {
        return 5;
    }
    if (out.pitch == fc::rc::DRONE_MID && out.yaw == fc::rc::DRONE_MID) {
        return 6;
    }

    return 0;
}
"""
    run_result = _compile_and_run_cpp(
        tmp_path, source, [command_receiver_impl, flight_controller_impl]
    )
    assert run_result.returncode == 0, run_result.stderr + run_result.stdout


def test_command_server_start_stop_is_safe(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    command_receiver_impl = repo_root / "src" / "fc" / "implementation" / "CommandReceiver.cpp"
    command_server_impl = repo_root / "src" / "fc" / "implementation" / "CommandServer.cpp"
    frame_codec_impl = repo_root / "src" / "fc" / "implementation" / "FrameCodec.cpp"

    source = r"""
#include "CommandServer.h"

#include <chrono>
#include <thread>

int main() {
    fc::CommandServer server(0);
    if (!server.start()) {
        server.stop();
        server.stop();
        return 0;
    }

    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    server.stop();
    server.stop();
    return 0;
}
"""
    run_result = _compile_and_run_cpp(
        tmp_path, source, [command_receiver_impl, command_server_impl, frame_codec_impl]
    )
    assert run_result.returncode == 0, run_result.stderr + run_result.stdout
