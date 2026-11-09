"""Проверка настройки исключений .ru без роутера."""

from pathlib import Path
import sys
from unittest.mock import patch
from werkzeug.datastructures import MultiDict

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
    outbounds = [
        {"tag": "local-vpn", "protocol": "vless"},
        {"tag": "reserve-1", "protocol": "vless"},
        {"tag": "reserve-2", "protocol": "vless"},
        {"tag": "direct", "protocol": "freedom"},
    ]
    with (
        patch.object(D, "keenetic_apply_begin", return_value=ctx) as begin,
        patch.object(D, "keenetic_get_outbounds", return_value=outbounds),
        patch.object(D, "keenetic_get_watchdog_targets", return_value={}) as read_targets,
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
            "RU_VPN_FAILOVER_TAGS": "", "RU_VPN_FAIL_DIRECT": "0",
        }
        data = MultiDict([
            ("domains", "example.ru"), ("tag", "local-vpn"), ("ru_chain_mode", "custom"),
            ("ru_reserve", "reserve-2"), ("ru_reserve", "reserve-1"), ("ru_fail_direct", "1"),
        ])
        apply.reset_mock()
        assert not client.post(path, data=data).json["ok"]  # v45 does not handle a reserve chain
        apply.assert_not_called()
        ctx["watchdog_sync"]["remote_ver"] = 46
        for invalid in ("direct", "block", "local-vpn", "bad tag"):
            bad = data.copy()
            bad.setlist("ru_reserve", [invalid])
            assert client.post(path, data=bad).status_code == 400
        bad = data.copy()
        bad.setlist("ru_reserve", ["absent"])
        assert not client.post(path, data=bad).json["ok"]
        apply.assert_not_called()
        assert client.post(path, data=data).json["ok"]
        assert apply.call_args.kwargs["config_updates"]["RU_VPN_FAILOVER_TAGS"] == "reserve-2 reserve-1"
        assert apply.call_args.kwargs["config_updates"]["RU_VPN_FAIL_DIRECT"] == "1"
        read_targets.return_value = {"ru_vpn_failover_tags": ["reserve-1"]}
        apply.reset_mock()
        legacy = {"domains": "example.ru", "tag": "reserve-1"}
        assert client.post(path, data=legacy).status_code == 409
        apply.assert_not_called()
        legacy["tag"] = "local-vpn"
        assert client.post(path, data=legacy).json["ok"]
        assert "RU_VPN_FAILOVER_TAGS" not in apply.call_args.kwargs["config_updates"]
        data.setlist("ru_chain_mode", ["single"])
        assert client.post(path, data=data).json["ok"]
        assert apply.call_args.kwargs["config_updates"]["RU_VPN_FAILOVER_TAGS"] == ""
        assert apply.call_args.kwargs["config_updates"]["RU_VPN_FAIL_DIRECT"] == "0"
        apply.return_value = {"ok": False, "stderr": "xray -test"}
        assert not client.post(path, data={"domains": "example.ru", "tag": "local-vpn"}).json["ok"]

    config = {"RU_VPN_DOMAINS": "example.ru blocked.ru direct.ru", "RU_VPN_TAG": "local-vpn",
              "RU_VPN_FAILOVER_TAGS": "reserve-1 reserve-2",
              "RU_VPN_SUBDOMAINS": "0", "BLOCK_DOMAINS": "blocked.ru", "DIRECT_DOMAINS": "direct.ru"}
    with patch.object(D, "keenetic_read_watchdog_config", return_value=config):
        targets = D.keenetic_get_watchdog_targets(strict=True)
        assert D._channel_for_domain("example.ru", targets)["tag"] is None
        assert D._channel_for_domain("example.ru", targets, "reserve-1")["tag"] == "reserve-1"
        assert D._channel_for_domain("example.ru", targets, "direct")["tag"] == "direct"
        assert D._channel_for_domain("www.example.ru", targets)["tag"] == "direct"
        assert D._channel_for_domain("blocked.ru", targets)["tag"] == "block"
        assert D._channel_for_domain("direct.ru", targets)["tag"] == "direct"
        config["RU_VPN_SUBDOMAINS"] = "1"
        targets = D.keenetic_get_watchdog_targets(strict=True)
        assert D._channel_for_domain("www.example.ru", targets, "block")["tag"] == "block"
        assert not D.keenetic_remove_outbound("local-vpn")["ok"]
        assert D.keenetic_remove_outbounds_bulk(["local-vpn"])["skipped"]["local-vpn"]
        assert targets["ru_vpn_failover_tags"] == ["reserve-1", "reserve-2"]
        assert not D.keenetic_remove_outbound("reserve-1")["ok"]
        assert D.keenetic_remove_outbounds_bulk(["reserve-1"])["skipped"]["reserve-1"]

    routing = '''// XKeen .ru VPN ["reserve-1",["domain:example.ru"]]
    {
      "routing": {"rules": [
        {"inboundTag": ["redirect", "tproxy"], "outboundTag": "direct", "domain": ["domain:example.ru"]},
        {"inboundTag": ["redirect", "tproxy"], "outboundTag": "reserve-1", "domain": ["domain:example.ru"]},
        {"inboundTag": ["redirect", "tproxy"], "outboundTag": "default"}
      ]}
    }'''
    with patch.object(D, "keenetic_read_file", return_value=routing) as read_routing:
        active = D.keenetic_get_routing_active()
        assert active["ru_vpn_active"] == "reserve-1"
        matching = {"ru_vpn_domains": ["example.ru"], "ru_vpn_subdomains": True,
                    "ru_vpn_tag": "local-vpn", "ru_vpn_failover_tags": ["reserve-1"]}
        assert D._ru_vpn_active_for_targets(active, matching) == "reserve-1"
        assert D._ru_vpn_active_for_targets(active, {**matching, "ru_vpn_domains": ["new.ru"]}) is None
        assert D._ru_vpn_active_for_targets(active, {**matching, "ru_vpn_failover_tags": ["reserve-2"]}) is None
        read_routing.return_value = routing.replace("// XKeen .ru VPN", "// ordinary rule")
        assert D.keenetic_get_routing_active()["ru_vpn_active"] is None

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
