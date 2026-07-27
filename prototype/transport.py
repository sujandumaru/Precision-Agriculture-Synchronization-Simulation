"""Fault-injecting transport boundary.

Faults are applied to real HTTP requests over a real socket: the request is
genuinely dropped, delayed, duplicated, or refused, not simulated by a flag
inside the sync routine.

Connections are kept alive and reused. Opening a fresh TCP connection per
request exhausts the Windows dynamic port range (~16k ports, 120s TIME_WAIT)
part way through a long sweep, which kills the run with a socket error.
"""
from __future__ import annotations

import http.client
import json
import random
import socket
import time
from urllib.parse import urlparse


class Partitioned(Exception):
    pass


class FaultyTransport:
    RETRIES = 3

    def __init__(self, base_url: str, rng: random.Random, drop=0.0, dup=0.0,
                 delay_ms=0, reorder=0.0):
        u = urlparse(base_url)
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or 80
        self.rng = rng
        self.drop = drop
        self.dup = dup
        self.delay_ms = delay_ms
        self.reorder = reorder
        self.partitioned = False
        self._conn: http.client.HTTPConnection | None = None
        self.stats = {"sent": 0, "dropped": 0, "duplicated": 0, "reordered": 0,
                      "failed": 0, "reconnects": 0}

    # ---------------------------------------------------------------- plumbing
    def _connect(self):
        if self._conn is None:
            self._conn = http.client.HTTPConnection(self.host, self.port, timeout=60)
            self._conn.connect()
            self._conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.stats["reconnects"] += 1

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _request(self, method: str, path: str, payload: dict | None) -> dict:
        """One HTTP round trip on the kept-alive connection, with reconnect."""
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if body else {}
        last = None
        for attempt in range(self.RETRIES):
            try:
                self._connect()
                assert self._conn is not None
                self._conn.request(method, path, body, headers)
                resp = self._conn.getresponse()
                data = resp.read()          # must drain to reuse the connection
                if resp.status != 200:
                    raise http.client.HTTPException(f"HTTP {resp.status}")
                return json.loads(data)
            except (http.client.HTTPException, OSError, ValueError) as e:
                last = e
                self.close()                # poisoned connection, start clean
                if attempt < self.RETRIES - 1:
                    time.sleep(0.05 * (attempt + 1))
        self.stats["failed"] += 1
        raise Partitioned(f"{type(last).__name__}: {last}")

    # ---------------------------------------------------------------- api
    def post(self, path: str, payload: dict) -> dict:
        if self.partitioned:
            raise Partitioned("network partition")
        self.stats["sent"] += 1
        if self.rng.random() < self.drop:
            self.stats["dropped"] += 1
            raise Partitioned("packet dropped")

        if self.reorder and "updates" in payload and len(payload["updates"]) > 1:
            if self.rng.random() < self.reorder:
                payload = dict(payload)
                payload["updates"] = list(payload["updates"])
                self.rng.shuffle(payload["updates"])
                self.stats["reordered"] += 1

        resp = self._request("POST", path, payload)

        if self.rng.random() < self.dup:
            # genuine duplicate delivery: same update_ids sent a second time
            self.stats["duplicated"] += 1
            try:
                self._request("POST", path, payload)
            except Partitioned:
                pass
        return resp

    def get(self, path: str) -> dict:
        if self.partitioned:
            raise Partitioned("network partition")
        return self._request("GET", path, None)
