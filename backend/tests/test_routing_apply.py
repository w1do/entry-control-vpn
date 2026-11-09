from app.services.routing_apply import merge_both_configs


def test_merge_both_configs_keeps_entry_and_exit_inbounds() -> None:
    entry = {
        "inbounds": [{"tag": "vless-in", "port": 443}],
        "outbounds": [{"tag": "block"}, {"tag": "direct"}, {"tag": "exit-x"}],
        "routing": {"rules": [{"type": "field", "outboundTag": "block"}]},
        "dns": {"servers": [{"address": "https://1.1.1.1/dns-query"}]},
    }
    exit_config = {"inbounds": [{"tag": "interconnect-in", "port": 18443}]}

    merged = merge_both_configs(entry, exit_config)

    tags = [inbound["tag"] for inbound in merged["inbounds"]]
    assert tags == ["vless-in", "interconnect-in"]
    assert merged["outbounds"] == entry["outbounds"]
    assert merged["dns"] == entry["dns"]
    assert merged["routing"]["rules"][0] == {
        "type": "field",
        "inboundTag": ["interconnect-in"],
        "outboundTag": "direct",
    }
    assert merged["routing"]["rules"][1] == {"type": "field", "outboundTag": "block"}
