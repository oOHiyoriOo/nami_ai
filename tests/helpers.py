"""Shared test helpers for async mock patterns used across multiple test files."""


class AsyncContextManagerMock:
    """Mock that works as `async with mock as obj:`."""
    def __init__(self, enter_return):
        self._enter_return = enter_return

    async def __aenter__(self):
        return self._enter_return

    async def __aexit__(self, *args):
        pass


class AsyncIteratorMock:
    """Mock async iterator for `async for record in result:`."""
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)
