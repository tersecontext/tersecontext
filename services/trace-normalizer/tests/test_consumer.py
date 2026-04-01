import pytest
from shared.consumer import RedisConsumerBase
from app.consumer import NormalizerConsumer


def test_normalizer_consumer_inherits_base():
    assert issubclass(NormalizerConsumer, RedisConsumerBase)
    assert NormalizerConsumer.stream == "stream:raw-traces"
    assert NormalizerConsumer.group == "normalizer-group"
