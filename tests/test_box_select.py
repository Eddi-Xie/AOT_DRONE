from src.vision.box_select import pick_best_box
from src.vision.types import Detection, PixelBBox


def _det(x: float, y: float, w: float, h: float, conf: float = 0.9) -> Detection:
    return Detection(
        bbox=PixelBBox(x=x, y=y, w=w, h=h),
        confidence=conf,
        track_id=1,
    )


def test_pick_best_box_prefers_near_desired_point_when_areas_are_similar() -> None:
    detections = (
        _det(x=450.0, y=450.0, w=80.0, h=80.0),
        _det(x=760.0, y=450.0, w=80.0, h=80.0),
    )

    best = pick_best_box(
        detections=detections,
        img_w=1000,
        img_h=1000,
        desired_cx=0.5,
        desired_cy=0.5,
    )

    assert best == detections[0]


def test_pick_best_box_area_bonus_can_outweigh_distance() -> None:
    detections = (
        _det(x=675.0, y=475.0, w=50.0, h=50.0),
        _det(x=0.0, y=0.0, w=600.0, h=600.0),
    )

    best = pick_best_box(
        detections=detections,
        img_w=1000,
        img_h=1000,
        desired_cx=0.5,
        desired_cy=0.5,
    )

    assert best == detections[1]


def test_pick_best_box_changes_with_desired_point() -> None:
    left = _det(x=250.0, y=450.0, w=100.0, h=100.0)
    right = _det(x=650.0, y=450.0, w=100.0, h=100.0)

    best_left = pick_best_box(
        detections=(left, right),
        img_w=1000,
        img_h=1000,
        desired_cx=0.2,
        desired_cy=0.5,
    )
    best_right = pick_best_box(
        detections=(left, right),
        img_w=1000,
        img_h=1000,
        desired_cx=0.8,
        desired_cy=0.5,
    )

    assert best_left == left
    assert best_right == right
