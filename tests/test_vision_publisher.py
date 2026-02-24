import json

from src.vision.publisher import create_publisher
from src.vision.types import NormalizedBBox, VisMessage, VisState


def test_jsonl_output_is_newline_delimited_json(tmp_path) -> None:
    output_path = tmp_path / "vis.jsonl"
    publisher = create_publisher(no_output=True, output_spec=f"jsonl:{output_path}")

    publisher.publish(
        VisMessage(
            ts=1.0,
            state=VisState.NO_TARGET,
            bbox=NormalizedBBox.zero(),
            conf=0.0,
            track_id=0,
            frame_id=1,
        )
    )
    publisher.publish(
        VisMessage(
            ts=2.0,
            state=VisState.TRACKING,
            bbox=NormalizedBBox(cx=0.5, cy=0.5, w=0.2, h=0.2),
            conf=0.8,
            track_id=7,
            frame_id=2,
        )
    )
    publisher.close()

    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]

    assert parsed[0]["type"] == "VIS"
    assert parsed[0]["frame_id"] == 1
    assert parsed[1]["type"] == "VIS"
    assert parsed[1]["frame_id"] == 2
