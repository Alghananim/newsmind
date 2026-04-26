# -*- coding: utf-8 -*-
"""Async batched writer for non-critical events.

Critical writes (trades, bugs) bypass this — they go synchronous.
Wait/observable events go through the queue, batched every flush_interval_ms.
"""
from __future__ import annotations
import threading
import queue
import time
from typing import Callable, Optional


class AsyncWriter:
    def __init__(self, write_fn: Callable, flush_interval_ms: int = 100,
                 batch_size: int = 50):
        self.write_fn = write_fn
        self.flush_interval_ms = flush_interval_ms
        self.batch_size = batch_size
        self.queue: queue.Queue = queue.Queue(maxsize=10000)
        self.dropped = 0
        self.written = 0
        self.submitted = 0
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def submit(self, event):
        try:
            self.queue.put_nowait(event)
            self.submitted += 1
        except queue.Full:
            self.dropped += 1

    def _loop(self):
        batch = []
        last_flush = time.time()
        while not self._stop:
            try:
                ev = self.queue.get(timeout=self.flush_interval_ms / 1000.0)
                batch.append(ev)
            except queue.Empty:
                pass
            now = time.time()
            if batch and (len(batch) >= self.batch_size or
                          (now - last_flush) * 1000 >= self.flush_interval_ms):
                for e in batch:
                    try:
                        self.write_fn(e)
                        self.written += 1
                    except Exception: self.dropped += 1
                batch = []
                last_flush = now
        # Drain on stop
        while not self.queue.empty():
            try:
                e = self.queue.get_nowait()
                try: self.write_fn(e)
                except Exception: self.dropped += 1
            except queue.Empty: break
        for e in batch:
            try: self.write_fn(e)
            except Exception: self.dropped += 1

    def backlog(self) -> int:
        return self.queue.qsize()

    def flush(self, timeout_s: float = 1.0):
        """Block until written counter catches up to submitted counter."""
        deadline = time.time() + timeout_s
        target = self.submitted
        while time.time() < deadline:
            if self.written + self.dropped >= target:
                return
            time.sleep(0.005)

    def stop(self):
        self._stop = True
        self._thread.join(timeout=2.0)
