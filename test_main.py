import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main


class FakeMessage:
    def __init__(self):
        self.reply_markup = None
        self.answers = []

    async def edit_reply_markup(self, reply_markup):
        self.reply_markup = reply_markup

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


class DeleteGroupCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_database_path = main.DATABASE_PATH
        self.temp_directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        main.DATABASE_PATH = Path(self.temp_directory.name) / "queue.db"
        main.group_queues.clear()
        main.swap_requests.clear()
        main.swap_requesters.clear()
        main.init_database()

    async def asyncTearDown(self):
        main.DATABASE_PATH = self.original_database_path
        self.temp_directory.cleanup()

    async def test_admin_callback_deletes_group_and_refreshes_keyboard(self):
        group_id = main.create_group("Test group")
        main.assign_user_id_to_group(123, "test_user", group_id)
        group = main.get_group_queue(group_id)
        pending_task = asyncio.create_task(asyncio.Event().wait())
        group.schedule_tasks["open"] = pending_task

        message = FakeMessage()
        callback = SimpleNamespace(
            data=f"group:delete:{group_id}",
            from_user=SimpleNamespace(username="admin"),
            message=message,
            answer=AsyncMock(),
        )

        with patch.object(main, "Message", FakeMessage), patch.object(
            main, "is_admin_user", return_value=True
        ):
            await main.delete_group_callback(callback)

        self.assertIsNone(main.get_user_group_id(123))
        self.assertNotIn(group_id, main.group_queues)
        self.assertTrue(pending_task.cancelled() or pending_task.cancelling())
        callback.answer.assert_awaited_once_with("Группа удалена.")
        self.assertEqual(
            message.reply_markup.inline_keyboard[0][0].callback_data,
            "group:admin_new",
        )

    async def test_stale_group_callback_reports_missing_group(self):
        message = FakeMessage()
        callback = SimpleNamespace(
            data="group:delete:999",
            from_user=SimpleNamespace(username="admin"),
            message=message,
            answer=AsyncMock(),
        )

        with patch.object(main, "Message", FakeMessage), patch.object(
            main, "is_admin_user", return_value=True
        ):
            await main.delete_group_callback(callback)

        callback.answer.assert_awaited_once_with(
            "Группа больше не существует.", show_alert=True
        )
        self.assertIsNotNone(message.reply_markup)

    async def test_group_selection_callback_assigns_user_and_replies(self):
        group_id = main.create_group("Selectable group")
        message = FakeMessage()
        callback = SimpleNamespace(
            data=f"group:{group_id}",
            from_user=SimpleNamespace(
                id=456,
                username="test_user",
                full_name="Test User",
            ),
            message=message,
            answer=AsyncMock(),
        )
        state = SimpleNamespace(clear=AsyncMock())

        with patch.object(main, "Message", FakeMessage):
            await main.group_callback(callback, state)

        self.assertEqual(main.get_user_group_id(456), group_id)
        self.assertIn("Selectable group", message.answers[0][0])
        callback.answer.assert_awaited_once_with()
        state.clear.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
