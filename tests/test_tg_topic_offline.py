"""Check Telegram topic settings and senders without contacting Telegram or Keenetic."""

import base64
import json
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import sub_expiry_notifier as notifier


SETTINGS = "/api/keenetic/tg-settings"
TEST = "/api/keenetic/tg-test"
TOKEN = "1234:" + "A" * 24
CHAT = "-10012345678"
TOPIC = "42"
CONFIG = {"TG_BOT_TOKEN": TOKEN, "TG_CHAT_ID": CHAT, "TG_MESSAGE_THREAD_ID": TOPIC, "PRIMARY_TAG": "demo"}


def check_upgrade(client):
    old_config = {key: value for key, value in CONFIG.items() if key != "TG_MESSAGE_THREAD_ID"}
    old_header = {"ok": True, "stdout": "# XKeen failover watchdog v46"}
    ok = {"ok": True, "stdout": "", "stderr": ""}
    address = {"token": TOKEN, "chat_id": CHAT, "thread_id": TOPIC}
    watchdog = Path(D.__file__).parent / "bootstrap" / "watchdog.sh.cur"

    with patch.object(D, "keenetic_ssh") as ssh:
        ssh.side_effect = [old_header, ok, {**ok, "stdout": "SYNTAX_FAIL"}]
        rejected = client.post(SETTINGS, json=address).json
        assert not rejected["ok"] and "v47" in rejected["stderr"]
        assert ssh.call_count == 3  # До чтения и записи настроек дело не дошло.

        ssh.reset_mock()
        ssh.side_effect = [
            old_header, ok, {**ok, "stdout": "OK_SWAPPED"},
            {**ok, "stdout": D._build_watchdog_config(old_config)},
            ok, ok, ok,
        ]
        assert client.post(SETTINGS, json=address).json["ok"]
        _, upload, swap, read, backup, write, apply = ssh.call_args_list
        assert base64.b64decode(upload.kwargs["stdin_data"]) == watchdog.read_bytes()
        assert swap.args[0].startswith(f"sh -n {D.WATCHDOG_REMOTE_PATH}.vnew && cp ")
        assert "cat /opt/etc/xray/watchdog.config" in read.args[0]
        assert backup.args[0].startswith("cp /opt/etc/xray/watchdog.config ")
        assert write.args[0] == "cat > /opt/etc/xray/watchdog.config"
        assert D._parse_watchdog_config(write.kwargs["stdin_data"])["TG_MESSAGE_THREAD_ID"] == TOPIC
        assert apply.args[0] == D.WATCHDOG_REMOTE_PATH


def main():
    D.app.config["TESTING"] = True
    client = D.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True

    check_upgrade(client)

    with (
        patch.object(D, "keenetic_read_watchdog_config", side_effect=lambda: dict(CONFIG)),
        patch.object(D, "keenetic_write_watchdog_config", return_value={"ok": True}) as write,
    ):
        assert client.get(SETTINGS).json["thread_id"] == TOPIC
        address = {"token": TOKEN, "chat_id": CHAT}
        for invalid in ("0", "-1", "12:34", "12_34", "１２", True):
            assert client.post(SETTINGS, json={**address, "thread_id": invalid}).status_code == 400
        assert client.post(SETTINGS, json={"thread_id": TOPIC}).status_code == 400
        write.assert_not_called()
        assert client.post(SETTINGS, json={**address, "thread_id": 42}).json["ok"]
        assert write.call_args.args[0] == {
            "TG_BOT_TOKEN": TOKEN, "TG_CHAT_ID": CHAT, "TG_MESSAGE_THREAD_ID": TOPIC,
        }
        assert client.post(SETTINGS, json={"token": "", "chat_id": "", "thread_id": ""}).json["ok"]
        assert all(value == "" for value in write.call_args.args[0].values())

    with (
        patch.object(D, "keenetic_read_watchdog_config", side_effect=lambda: dict(CONFIG)),
        patch.object(D, "keenetic_ensure_watchdog_current", return_value={"action": "failed", "to": 47}) as watchdog,
        patch.object(D, "keenetic_ssh", return_value={"ok": True}) as ssh,
    ):
        assert not D.keenetic_write_watchdog_config({"TG_MESSAGE_THREAD_ID": TOPIC}, stage_id="fixture")["ok"]
        ssh.assert_not_called()
        watchdog.return_value = {"action": "upgraded", "to": 47}
        assert D.keenetic_write_watchdog_config({"TG_MESSAGE_THREAD_ID": TOPIC}, stage_id="fixture")["ok"]
        assert D._parse_watchdog_config(ssh.call_args.kwargs["stdin_data"])["TG_MESSAGE_THREAD_ID"] == TOPIC
        assert D.keenetic_write_watchdog_config({"TG_BOT_TOKEN": TOKEN, "TG_CHAT_ID": "-10098765432"}, stage_id="fixture")["ok"]
        assert D._parse_watchdog_config(ssh.call_args.kwargs["stdin_data"])["TG_MESSAGE_THREAD_ID"] == ""

    response = {"ok": True, "stdout": json.dumps({"ok": True, "result": {"message_id": 12, "message_thread_id": 42}})}
    with (
        patch.object(D, "keenetic_read_watchdog_config", return_value=CONFIG),
        patch.object(D, "keenetic_ssh", return_value=response) as ssh,
        patch.object(D, "keenetic_ensure_socks_local", return_value={"ok": True}),
        patch.object(D, "keenetic_ensure_socks_route", return_value={"ok": True}),
        patch.object(D, "keenetic_ensure_watchdog_current", return_value={"action": "current", "remote_ver": 47}),
    ):
        assert client.post(TEST, json={"thread_id": "1; bad"}).status_code == 400
        ssh.assert_not_called()
        assert client.post(TEST, json={}).json["info"].endswith("тему 42.")
        assert "message_thread_id=42" in ssh.call_args.args[0]
        assert client.post(TEST, json={"thread_id": ""}).json["ok"]
        assert "message_thread_id=" not in ssh.call_args.args[0]
        assert client.post(TEST, json={"chat_id": "-10098765432"}).json["ok"]
        assert "message_thread_id=" not in ssh.call_args.args[0]
        assert client.post("/api/keenetic/repair-tg-channel", json={}).json["test_sent"]
        assert "message_thread_id=42" in ssh.call_args.args[0]

    with patch.object(notifier.urllib.request, "urlopen") as send, patch.object(notifier, "log"):
        send.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'
        assert notifier.send_telegram(TOKEN, CHAT, "test", TOPIC)
        assert parse_qs(send.call_args.args[0].data.decode())["message_thread_id"] == [TOPIC]
        assert notifier.send_telegram(TOKEN, CHAT, "test")
        assert "message_thread_id" not in parse_qs(send.call_args.args[0].data.decode())
        send.reset_mock()
        assert not notifier.send_telegram(TOKEN, CHAT, "test", "invalid")
        send.assert_not_called()

    print("test_tg_topic_offline: OK")


if __name__ == "__main__":
    main()
