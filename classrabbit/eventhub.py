"""Thread-to-async fanout for already-persisted reopening events."""

import asyncio
import threading


class EventHub:
    def __init__(self):
        self.lock = threading.Lock()
        self.subscribers = {}

    def subscribe(self):
        queue = asyncio.Queue(maxsize=100)
        with self.lock:
            self.subscribers[queue] = asyncio.get_running_loop()
        return queue

    def unsubscribe(self, queue):
        with self.lock:
            self.subscribers.pop(queue, None)

    @staticmethod
    def enqueue(queue, event):
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(event)

    def publish(self, event):
        with self.lock:
            subscribers = list(self.subscribers.items())
        for queue, loop in subscribers:
            try:
                loop.call_soon_threadsafe(self.enqueue, queue, event)
            except RuntimeError:
                self.unsubscribe(queue)
