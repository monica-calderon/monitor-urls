import sys
import tempfile
import unittest
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

        client.send_message("<b>Cambio</b> &amp; aviso")

        self.assertEqual(len(session.posts), 1)
        post = session.posts[0]
        self.assertEqual(post["url"], "https://ntfy.example/alerts")
        self.assertEqual(post["content"], "Cambio & aviso")
        self.assertEqual(
            post["headers"],
            {
                "Authorization": "Bearer secret-token",
                "Priority": "high",
                "Tags": "warning,house",
            },
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

            client.send_document(path, "<b>Texto extraido</b> &amp; listo")

        self.assertEqual(len(session.puts), 1)
        put = session.puts[0]
        self.assertEqual(put["url"], "https://ntfy.example/alerts")
        self.assertEqual(put["content"], b"contenido privado")
        self.assertEqual(put["auth"], None)
        self.assertEqual(
            put["headers"],
            {
                "Authorization": "Bearer secret-token",
                "Priority": "high",
                "Tags": "warning,house",
                "Filename": "reporte.txt",
                "Message": "Texto extraido & listo",
                "Content-Type": "text/plain",
            },
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
