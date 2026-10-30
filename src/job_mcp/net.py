"""Asking the job-board APIs for data, politely.

Every request has a time limit, and when a server is busy ("429 Too Many Requests", "503 Service
Unavailable") or the connection drops, we wait a little and try again, a couple of times.

This module knows nothing about MCP, so it can be used and tested as plain Python.
"""

import asyncio
import json
import random

import httpx

HEADERS = {
    # Say honestly who we are.
    "User-Agent": "job-mcp/0.1 (+https://github.com/Jal246/job-mcp)",
    "Accept": "application/json",
}
TIMEOUT_SECONDS = 30

# How many times to try a request in total, and how long to wait before the 2nd and 3rd tries
# (doubling each time, plus a bit of randomness so many clients don't all retry at once).
ATTEMPTS = 3
FIRST_RETRY_WAIT = 1.0

# Server answers that mean "busy or broken right now", so trying again later might work.
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# Tests swap in a fake network (httpx.MockTransport) and a sleep that doesn't really wait.
TRANSPORT: httpx.AsyncBaseTransport | None = None
SLEEP = asyncio.sleep


class NetError(Exception):
    """A request that failed, with a message the user can read."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code  # The HTTP status, if the server answered.


class NotFound(NetError):
    """The server answered 404: there's no such board."""


async def get_json(url: str) -> dict | list:
    """GET url and return its JSON, retrying if the server is busy.

    Raises NotFound for a 404, and NetError for anything else that goes wrong.
    """
    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT_SECONDS, transport=TRANSPORT,
                                 follow_redirects=True) as client:
        for attempt in range(ATTEMPTS):
            last = attempt == ATTEMPTS - 1
            try:
                response = await client.get(url)
            except httpx.HTTPError as error:
                if last:
                    raise NetError(f"couldn't reach {httpx.URL(url).host}: {error!r}") from None
            else:
                if response.status_code == 404:
                    raise NotFound(f"{url} doesn't exist (404)", 404)
                if response.status_code not in RETRYABLE_STATUSES:
                    if response.status_code != 200:
                        raise NetError(f"{url} answered {response.status_code}",
                                       response.status_code)
                    try:
                        return json.loads(response.content)
                    except ValueError:
                        raise NetError(f"{url} didn't return valid JSON") from None
                if last:
                    raise NetError(f"{url} is busy ({response.status_code}); try again later",
                                   response.status_code)
            await SLEEP(FIRST_RETRY_WAIT * 2**attempt * random.uniform(1.0, 1.5))
    raise AssertionError("unreachable")
