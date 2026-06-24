import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import monitor


def settings(
    method: str,
    telegram: bool = False,
    ntfy: bool = False,
) -> monitor.NotificationSettings:
    return monitor.NotificationSettings(
        method=method,
        telegram=monitor.TelegramSettings(
            bot_token="telegram-token" if telegram else "",
            chat_id="telegram-chat" if telegram else "",
        ),
        ntfy=monitor.NtfySettings(
            topic="topic" if ntfy else "",
            server="https://ntfy.example",
        ),
    )


class FakeResponse:
    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.posts = []
        self.puts = []

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.posts.append({"url": url, **kwargs})
        return FakeResponse()

    def put(self, url: str, **kwargs: object) -> FakeResponse:
        self.puts.append({"url": url, **kwargs})
        return FakeResponse()


def sample_configs() -> list[dict[str, object]]:
    return [
        {
            "name": "Auto",
            "url": "https://example.com/auto",
            "mode": "auto",
        },
        {
            "name": "Manual",
            "url": "https://example.com/manual",
            "mode": "manual_summary",
        },
    ]


class TelegramDeleteUrlTests(unittest.TestCase):
    def test_validate_action_to_run_accepts_delete_url(self) -> None:
        with patch("monitor.ACTION_TO_RUN", "delete-url"):
            self.assertEqual(monitor.validate_action_to_run(), "delete-url")

    def test_delete_selector_includes_auto_and_manual_urls(self) -> None:
        reply_markup = monitor.build_delete_url_reply_markup(sample_configs())

        keyboard = reply_markup["inline_keyboard"]
        self.assertEqual(len(keyboard), 2)
        self.assertEqual(keyboard[0][0]["text"], "Auto (auto)")
        self.assertEqual(keyboard[1][0]["text"], "Manual (manual)")
        self.assertEqual(
            keyboard[0][0]["callback_data"],
            "delete-url:" + monitor.delete_url_callback_token("https://example.com/auto"),
        )
        self.assertEqual(
            keyboard[1][0]["callback_data"],
            "delete-url:"
            + monitor.delete_url_callback_token("https://example.com/manual"),
        )

    def test_allowed_callback_deletes_matching_url(self) -> None:
        configs = sample_configs()
        token = monitor.delete_url_callback_token("https://example.com/manual")
        updates = [
            {
                "update_id": 1,
                "callback_query": {
                    "from": {"id": 12345},
                    "data": f"delete-url:{token}",
                },
            }
        ]

        with (
            patch("monitor.TELEGRAM_ALLOWED_USER_ID", "12345"),
            patch("monitor.TELEGRAM_CHAT_ID", "999"),
            patch("monitor.load_telegram_offset", return_value=0),
            patch("monitor.fetch_telegram_updates", return_value=updates),
            patch("monitor.update_monitor_urls_secret") as update_secret,
            patch("monitor.save_telegram_offset") as save_offset,
            patch("monitor.try_send_message") as send_message,
        ):
            result = monitor.process_telegram_updates(configs)

        self.assertEqual([config["name"] for config in result], ["Auto"])
        update_secret.assert_called_once()
        self.assertEqual(update_secret.call_args.args[0], result)
        save_offset.assert_called_once_with(1)
        self.assertIn("URL eliminada", send_message.call_args.args[0])

    def test_callback_from_unauthorized_user_does_not_delete(self) -> None:
        configs = sample_configs()
        token = monitor.delete_url_callback_token("https://example.com/manual")
        updates = [
            {
                "update_id": 1,
                "callback_query": {
                    "from": {"id": 54321},
                    "data": f"delete-url:{token}",
                },
            }
        ]

        with (
            patch("monitor.TELEGRAM_ALLOWED_USER_ID", "12345"),
            patch("monitor.load_telegram_offset", return_value=0),
            patch("monitor.fetch_telegram_updates", return_value=updates),
            patch("monitor.update_monitor_urls_secret") as update_secret,
            patch("monitor.save_telegram_offset") as save_offset,
            patch("monitor.try_send_message") as send_message,
        ):
            result = monitor.process_telegram_updates(configs)

        self.assertEqual(result, configs)
        update_secret.assert_not_called()
        send_message.assert_not_called()
        save_offset.assert_called_once_with(1)

    def test_unknown_callback_hash_does_not_update_secret(self) -> None:
        configs = sample_configs()
        updates = [
            {
                "update_id": 1,
                "callback_query": {
                    "from": {"id": 12345},
                    "data": "delete-url:0000000000000000",
                },
            }
        ]

        with (
            patch("monitor.TELEGRAM_ALLOWED_USER_ID", "12345"),
            patch("monitor.load_telegram_offset", return_value=0),
            patch("monitor.fetch_telegram_updates", return_value=updates),
            patch("monitor.update_monitor_urls_secret") as update_secret,
            patch("monitor.save_telegram_offset") as save_offset,
            patch("monitor.try_send_message") as send_message,
        ):
            result = monitor.process_telegram_updates(configs)

        self.assertEqual(result, configs)
        update_secret.assert_not_called()
        save_offset.assert_called_once_with(1)
        self.assertIn("URL no encontrada", send_message.call_args.args[0])

    def test_callback_update_does_not_block_add_url_message(self) -> None:
        configs = sample_configs()
        updates = [
            {
                "update_id": 1,
                "callback_query": {
                    "from": {"id": 12345},
                    "data": "delete-url:0000000000000000",
                },
            },
            {
                "update_id": 2,
                "message": {
                    "from": {"id": 12345},
                    "text": "https://new.example/path",
                },
            },
        ]

        with (
            patch("monitor.TELEGRAM_ALLOWED_USER_ID", "12345"),
            patch("monitor.load_telegram_offset", return_value=0),
            patch("monitor.fetch_telegram_updates", return_value=updates),
            patch("monitor.update_monitor_urls_secret") as update_secret,
            patch("monitor.save_telegram_offset") as save_offset,
            patch("monitor.try_send_message"),
        ):
            result = monitor.process_telegram_updates(configs)

        self.assertEqual(len(result), 3)
        self.assertEqual(result[-1]["url"], "https://new.example/path")
        update_secret.assert_called_once()
        save_offset.assert_called_once_with(2)


class NotificationSelectionTests(unittest.TestCase):
    def test_auto_uses_all_complete_channels(self) -> None:
        selected = monitor.select_notification_channels(
            settings("auto", telegram=True, ntfy=True)
        )

        self.assertEqual(selected, ["telegram", "ntfy"])

    def test_auto_skips_incomplete_channels(self) -> None:
        selected = monitor.select_notification_channels(
            settings("auto", telegram=False, ntfy=True)
        )

        self.assertEqual(selected, ["ntfy"])

    def test_telegram_method_uses_only_telegram(self) -> None:
        selected = monitor.select_notification_channels(
            settings("telegram", telegram=True, ntfy=True)
        )

        self.assertEqual(selected, ["telegram"])

    def test_ntfy_method_uses_only_ntfy_and_requires_topic(self) -> None:
        selected = monitor.select_notification_channels(
            settings("ntfy", telegram=True, ntfy=True)
        )

        self.assertEqual(selected, ["ntfy"])
        with self.assertRaisesRegex(RuntimeError, "NTFY_TOPIC"):
            monitor.select_notification_channels(settings("ntfy", ntfy=False))

    def test_both_method_requires_and_uses_both(self) -> None:
        selected = monitor.select_notification_channels(
            settings("both", telegram=True, ntfy=True)
        )

        self.assertEqual(selected, ["telegram", "ntfy"])
        with self.assertRaisesRegex(RuntimeError, "requiere"):
            monitor.select_notification_channels(settings("both", telegram=True))

    def test_invalid_method_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "NTFY_METHOD"):
            monitor.select_notification_channels(settings("bad", telegram=True))


class NtfyClientTests(unittest.TestCase):
    def test_ntfy_send_posts_plain_text_without_network(self) -> None:
        session = FakeSession()
        client = monitor.NtfyClient(
            monitor.NtfySettings(
                topic="alerts",
                server="https://ntfy.example/",
                token="secret-token",
                priority="high",
                tags="warning,house",
            ),
            session=session,
        )

        message_uuid = uuid.UUID("00000000-0000-0000-0000-000000000001")
        with patch("monitor.uuid.uuid4", return_value=message_uuid):
            client.send_message("<b>Cambio</b> &amp; aviso")

        self.assertEqual(len(session.posts), 1)
        post = session.posts[0]
        sequence_id = "monitor-urls-00000000000000000000000000000001"
        self.assertEqual(post["url"], "https://ntfy.example/alerts")
        self.assertEqual(post["content"], "Cambio & aviso")
        self.assertEqual(post["headers"]["Authorization"], "Bearer secret-token")
        self.assertEqual(post["headers"]["Priority"], "high")
        self.assertEqual(post["headers"]["Tags"], "warning,house")
        self.assertEqual(post["headers"]["X-Sequence-ID"], sequence_id)
        self.assertIn("action=http", post["headers"]["Actions"])
        self.assertIn("label=Eliminar", post["headers"]["Actions"])
        self.assertIn(
            f"url=https://ntfy.example/alerts/{sequence_id}",
            post["headers"]["Actions"],
        )
        self.assertIn("method=DELETE", post["headers"]["Actions"])
        self.assertIn(
            'headers.Authorization="Bearer secret-token"',
            post["headers"]["Actions"],
        )

    def test_ntfy_send_message_adds_open_and_delete_actions(self) -> None:
        session = FakeSession()
        client = monitor.NtfyClient(
            monitor.NtfySettings(topic="alerts", server="https://ntfy.example/"),
            session=session,
        )
        message_uuid = uuid.UUID("00000000-0000-0000-0000-000000000002")

        with patch("monitor.uuid.uuid4", return_value=message_uuid):
            client.send_message(
                "Cambio detectado",
                monitor.NotificationOptions(open_url="https://example.com/casa"),
            )

        self.assertEqual(len(session.posts), 1)
        sequence_id = "monitor-urls-00000000000000000000000000000002"
        actions = session.posts[0]["headers"]["Actions"]
        self.assertEqual(session.posts[0]["headers"]["X-Sequence-ID"], sequence_id)
        self.assertIn("action=view", actions)
        self.assertIn("label=Abrir web", actions)
        self.assertIn("url=https://example.com/casa", actions)
        self.assertIn("action=http", actions)
        self.assertIn(
            f"url=https://ntfy.example/alerts/{sequence_id}",
            actions,
        )

    def test_ntfy_send_document_puts_file_without_network(self) -> None:
        session = FakeSession()
        client = monitor.NtfyClient(
            monitor.NtfySettings(
                topic="alerts",
                server="https://ntfy.example/",
                token="secret-token",
                priority="high",
                tags="warning,house",
            ),
            session=session,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "reporte.txt"
            path.write_bytes(b"contenido privado")

            document_uuid = uuid.UUID("00000000-0000-0000-0000-000000000003")
            with patch("monitor.uuid.uuid4", return_value=document_uuid):
                client.send_document(path, "<b>Texto extraido</b> &amp; listo")

        self.assertEqual(len(session.puts), 1)
        put = session.puts[0]
        sequence_id = "monitor-urls-00000000000000000000000000000003"
        self.assertEqual(put["url"], "https://ntfy.example/alerts")
        self.assertEqual(put["content"], b"contenido privado")
        self.assertEqual(put["auth"], None)
        self.assertEqual(put["headers"]["Authorization"], "Bearer secret-token")
        self.assertEqual(put["headers"]["Priority"], "high")
        self.assertEqual(put["headers"]["Tags"], "warning,house")
        self.assertEqual(put["headers"]["Filename"], "reporte.txt")
        self.assertEqual(put["headers"]["Message"], "Texto extraido & listo")
        self.assertEqual(put["headers"]["Content-Type"], "text/plain")
        self.assertEqual(put["headers"]["X-Sequence-ID"], sequence_id)
        self.assertIn("label=Eliminar", put["headers"]["Actions"])
        self.assertIn(
            f"url=https://ntfy.example/alerts/{sequence_id}",
            put["headers"]["Actions"],
        )
        self.assertIn(
            'headers.Authorization="Bearer secret-token"',
            put["headers"]["Actions"],
        )

    def test_ntfy_delete_action_uses_basic_auth_for_username_password(self) -> None:
        session = FakeSession()
        client = monitor.NtfyClient(
            monitor.NtfySettings(
                topic="alerts",
                server="https://ntfy.example/",
                username="user",
                password="pass",
            ),
            session=session,
        )
        message_uuid = uuid.UUID("00000000-0000-0000-0000-000000000004")

        with patch("monitor.uuid.uuid4", return_value=message_uuid):
            client.send_message("Mensaje")

        self.assertEqual(len(session.posts), 1)
        self.assertIn(
            'headers.Authorization="Basic dXNlcjpwYXNz"',
            session.posts[0]["headers"]["Actions"],
        )

    def test_ntfy_send_document_encodes_non_ascii_caption_header(self) -> None:
        session = FakeSession()
        client = monitor.NtfyClient(
            monitor.NtfySettings(topic="alerts", server="https://ntfy.example/"),
            session=session,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "reporte.txt"
            path.write_text("contenido", encoding="utf-8")

            client.send_document(path, "📄 Texto extraido")

        self.assertEqual(len(session.puts), 1)
        message_header = session.puts[0]["headers"]["Message"]
        self.assertIn("=?utf-8?", message_header)
        self.assertIn("Texto", message_header)

    def test_ntfy_send_document_dry_run_does_not_post(self) -> None:
        client = monitor.NtfyClient(
            monitor.NtfySettings(topic="alerts", server="https://ntfy.example/")
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "captura.png"
            path.write_bytes(b"png")

            with patch("monitor.DRY_RUN", True), patch("builtins.print") as mocked_print:
                client.send_document(path, "Screenshot")

        printed = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
        self.assertIn("DOCUMENTO NTFY DRY_RUN", printed)
        self.assertIn("captura.png", printed)


class NotificationRouterTests(unittest.TestCase):
    def test_router_sends_to_telegram_and_ntfy_when_both_configured(self) -> None:
        session = FakeSession()
        router = monitor.build_notification_router(
            settings("both", telegram=True, ntfy=True),
            ntfy_session=session,
        )

        with patch("monitor.send_telegram") as send_telegram:
            router.send_message("mensaje")

        send_telegram.assert_called_once()
        self.assertEqual(len(session.posts), 1)
        self.assertEqual(session.posts[0]["content"], "mensaje")

    def test_router_sends_document_to_telegram_and_ntfy_when_both_configured(self) -> None:
        session = FakeSession()
        router = monitor.build_notification_router(
            settings("both", telegram=True, ntfy=True),
            ntfy_session=session,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "reporte.txt"
            path.write_text("contenido", encoding="utf-8")

            with patch("monitor.send_telegram_document") as send_telegram_document:
                router.send_document(path, "caption")

        send_telegram_document.assert_called_once()
        self.assertEqual(len(session.puts), 1)
        self.assertEqual(session.puts[0]["content"], b"contenido")


if __name__ == "__main__":
    unittest.main()
