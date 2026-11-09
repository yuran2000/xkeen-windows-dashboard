"""Проверка настройки исключений .ru без роутера."""

from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dashboard as D


def main():
    template = (ROOT / "bootstrap/05_routing.template.json.cur").read_text(encoding="utf-8")
    D.app.config["TESTING"] = True
    client = D.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True

    path = "/api/xkeen/set-ru-vpn-exceptions"
    ctx = {"ok": True, "watchdog_sync": {"action": "current", "remote_ver": 45}}
    outbounds = [{"tag": "local-vpn", "protocol": "vless"}, {"tag": "direct", "protocol": "freedom"}]
    with (
        patch.object(D, "keenetic_apply_begin", return_value=ctx) as begin,
        patch.object(D, "keenetic_get_outbounds", return_value=outbounds),
        patch.object(D, "keenetic_read_file", return_value=template) as read_file,
        patch.object(D, "keenetic_apply_commit", return_value={"ok": True, "restart": "1"}) as apply,
    ):
        for name in ("*.example.ru", "https://example.ru", "example.com", "ru", "-example.ru"):
            assert client.post(path, data={"domains": name, "tag": "local-vpn"}).status_code == 400
        assert client.post(path, data={"domains": "example.ru", "tag": "direct"}).status_code == 400
        begin.assert_not_called()
        assert not client.post(path, data={"domains": "example.ru", "tag": "missing"}).json["ok"]
        read_file.return_value = "{}"
        assert not client.post(path, data={"domains": "example.ru", "tag": "local-vpn"}).json["ok"]
        read_file.return_value = template.replace("\n__DIRECT_RULE_BLOCK__\n", "\n").replace(
            "\n__AI_RULE_BLOCK__\n", "\n__DIRECT_RULE_BLOCK__\n__AI_RULE_BLOCK__\n"
        )
        assert not client.post(path, data={"domains": "example.ru", "tag": "local-vpn"}).json["ok"]
        begin.assert_not_called()
        read_file.return_value = template
        ctx["watchdog_sync"]["remote_ver"] = 44
        assert not client.post(path, data={"domains": "example.ru", "tag": "local-vpn"}).json["ok"]
        ctx["watchdog_sync"]["remote_ver"] = 45
        apply.assert_not_called()

        response = client.post(path, data={"domains": "EXAMPLE.ru. example.ru пример.ru faß.ru", "tag": "local-vpn"})
        assert response.json["ok"], response.json
        apply.assert_called_once_with(ctx, config_updates={
            "RU_VPN_DOMAINS": "example.ru xn--e1afmkfd.ru xn--fa-hia.ru",
            "RU_VPN_TAG": "local-vpn",
            "RU_VPN_SUBDOMAINS": "0",
        })
        assert client.post(path, data={"domains": "example.ru", "tag": "local-vpn", "subdomains": "1"}).json["ok"]
        assert apply.call_args.kwargs["config_updates"]["RU_VPN_SUBDOMAINS"] == "1"
        assert client.post(path, data={"domains": "", "tag": "local-vpn", "subdomains": "1"}).json["ok"]
        assert apply.call_args.kwargs["config_updates"] == {
            "RU_VPN_DOMAINS": "", "RU_VPN_TAG": "", "RU_VPN_SUBDOMAINS": "0",
        }
        apply.return_value = {"ok": False, "stderr": "xray -test"}
        assert not client.post(path, data={"domains": "example.ru", "tag": "local-vpn"}).json["ok"]

    config = {"RU_VPN_DOMAINS": "example.ru blocked.ru direct.ru", "RU_VPN_TAG": "local-vpn",
              "RU_VPN_SUBDOMAINS": "0", "BLOCK_DOMAINS": "blocked.ru", "DIRECT_DOMAINS": "direct.ru"}
    with patch.object(D, "keenetic_read_watchdog_config", return_value=config):
        targets = D.keenetic_get_watchdog_targets(strict=True)
        assert D._channel_for_domain("example.ru", targets)["tag"] == "local-vpn"
        assert D._channel_for_domain("www.example.ru", targets)["tag"] == "direct"
        assert D._channel_for_domain("blocked.ru", targets)["tag"] == "block"
        assert D._channel_for_domain("direct.ru", targets)["tag"] == "direct"
        config["RU_VPN_SUBDOMAINS"] = "1"
        targets = D.keenetic_get_watchdog_targets(strict=True)
        assert D._channel_for_domain("www.example.ru", targets)["tag"] == "local-vpn"
        assert not D.keenetic_remove_outbound("local-vpn")["ok"]
        assert D.keenetic_remove_outbounds_bulk(["local-vpn"])["skipped"]["local-vpn"]

    with patch.object(D, "is_keenetic_reachable", return_value=False):
        page = client.get("/xkeen")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert 'data-xk-sub="🔀 Домены .ru через VPN" data-xk-page-id="ru-vpn"' in html
        assert html.count('id="ru-vpn-exceptions"') == 1
        assert 'href="#ru-vpn-exceptions"' in html

    print("test_ru_vpn_exceptions: OK")


if __name__ == "__main__":
    main()
