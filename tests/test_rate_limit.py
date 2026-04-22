import asyncio
import time
import pytest
from knotch_mcp.rate_limit import TokenBucket


@pytest.fixture
def bucket():
    return TokenBucket(rate=10.0, capacity=10)


@pytest.mark.asyncio
async def test_acquire_within_capacity(bucket):
    for _ in range(10):
        await bucket.acquire()


@pytest.mark.asyncio
async def test_acquire_blocks_when_empty():
    bucket = TokenBucket(rate=100.0, capacity=1)
    await bucket.acquire()
    start = time.monotonic()
    await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.005


@pytest.mark.asyncio
async def test_bucket_refills_over_time():
    bucket = TokenBucket(rate=1000.0, capacity=2)
    await bucket.acquire()
    await bucket.acquire()
    await asyncio.sleep(0.01)
    await bucket.acquire()
