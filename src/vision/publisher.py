from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol, TextIO

from .types import VisMessage


class Publisher(Protocol):
    def publish(self, message: VisMessage) -> None: ...

    def close(self) -> None: ...


class NullPublisher:
    def publish(self, message: VisMessage) -> None:
        del message

    def close(self) -> None:
        return None


class StdoutPublisher:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout

    def publish(self, message: VisMessage) -> None:
        self._stream.write(message.to_json())
        self._stream.write("\n")
        self._stream.flush()

    def close(self) -> None:
        return None


class JsonlPublisher:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a", encoding="utf-8")

    def publish(self, message: VisMessage) -> None:
        self._handle.write(message.to_json())
        self._handle.write("\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


class MultiPublisher:
    def __init__(self, publishers: list[Publisher]) -> None:
        self._publishers = publishers

    def publish(self, message: VisMessage) -> None:
        for publisher in self._publishers:
            publisher.publish(message)

    def close(self) -> None:
        for publisher in self._publishers:
            publisher.close()


def create_publisher(no_output: bool = False, output_spec: str | None = None) -> Publisher:
    publishers: list[Publisher] = []
    if not no_output:
        publishers.append(StdoutPublisher())
    if output_spec is not None:
        publishers.append(_publisher_from_output_spec(output_spec))

    if not publishers:
        return NullPublisher()
    if len(publishers) == 1:
        return publishers[0]
    return MultiPublisher(publishers)


def _publisher_from_output_spec(spec: str) -> Publisher:
    if not spec.startswith("jsonl:"):
        raise ValueError("unsupported output format; expected jsonl:<path>")
    path_text = spec.split(":", 1)[1].strip()
    if not path_text:
        raise ValueError("jsonl output path is empty")
    return JsonlPublisher(Path(path_text))
