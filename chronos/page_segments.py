"""Deterministic physical-page partitioning; never cross document boundaries."""
POLICY_VERSION = "physical-three-v1"


def page_segments(count: int, *, boundaries: tuple[int, ...] = ()) -> tuple[tuple[int, int], ...]:
    if type(count) is not int or not 1 <= count <= 1000:
        raise ValueError("invalid page count")
    if any(type(p) is not int or not 1 <= p <= count for p in boundaries):
        raise ValueError("invalid concept boundary")
    result = []
    start = 1
    while start <= count:
        end = min(start + 2, count)
        candidates = [p for p in boundaries if p - start + 1 in (2, 3, 4)]
        if candidates:
            end = min(candidates, key=lambda p: (abs(p - end), p))
        result.append((start, end))
        start = end + 1
    return tuple(result)
