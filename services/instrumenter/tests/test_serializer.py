import json
import pytest
from app.serializer import safe_serialize


def test_primitives():
    assert json.loads(safe_serialize(42)) == 42
    assert json.loads(safe_serialize("hello")) == "hello"
    assert json.loads(safe_serialize(True)) is True
    assert json.loads(safe_serialize(None)) == None


def test_dict_and_list():
    result = json.loads(safe_serialize({"a": 1, "b": [2, 3]}))
    assert result == {"a": 1, "b": [2, 3]}


def test_nested_dict_depth_limit():
    deep = {"a": {"b": {"c": {"d": 1}}}}
    result = json.loads(safe_serialize(deep, max_depth=2))
    assert result["a"]["b"] == "<dict>"


def test_cycle_detection():
    d: dict = {}
    d["self"] = d
    result = json.loads(safe_serialize(d))
    assert result["self"] == "<circular>"


def test_truncation_at_max_bytes():
    big = {"data": "x" * 2000}
    result = safe_serialize(big, max_bytes=1024)
    assert len(result) <= 1024
    assert result.endswith('...[truncated]"')


def test_unserializable_object():
    import threading
    lock = threading.Lock()
    result = json.loads(safe_serialize(lock))
    assert result == "<Lock>"


def test_dataclass():
    from dataclasses import dataclass

    @dataclass
    class Point:
        x: int
        y: int

    result = json.loads(safe_serialize(Point(1, 2)))
    assert result == {"x": 1, "y": 2}


def test_pydantic_model():
    from pydantic import BaseModel

    class Item(BaseModel):
        name: str
        value: int

    result = json.loads(safe_serialize(Item(name="a", value=1)))
    assert result == {"name": "a", "value": 1}


def test_set_becomes_list():
    result = json.loads(safe_serialize({1, 2, 3}))
    assert sorted(result) == [1, 2, 3]


def test_tuple_becomes_list():
    result = json.loads(safe_serialize((1, 2)))
    assert result == [1, 2]
