import asyncio
import unittest

from classrabbit.eventhub import EventHub


class EventHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_thread_delivers_to_every_connected_browser(self):
        hub = EventHub()
        first, second = hub.subscribe(), hub.subscribe()
        event = {"id": 1, "class_id": "186827200"}
        await asyncio.to_thread(hub.publish, event)
        self.assertEqual(await asyncio.wait_for(first.get(), 1), event)
        self.assertEqual(await asyncio.wait_for(second.get(), 1), event)
        hub.unsubscribe(first)
        hub.unsubscribe(second)
        self.assertEqual(hub.subscribers, {})

    async def test_disconnected_browser_is_removed_and_buffer_is_bounded(self):
        hub = EventHub()
        queue = hub.subscribe()
        for event_id in range(120):
            hub.publish({"id": event_id})
        await asyncio.sleep(0)
        self.assertEqual(queue.qsize(), 100)
        self.assertEqual(queue.get_nowait()["id"], 20)
        hub.unsubscribe(queue)
        hub.publish({"id": 121})
        await asyncio.sleep(0)
        self.assertEqual(queue.qsize(), 99)
