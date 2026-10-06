"""Unit tests for the Blend Network status summary."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from dashboard import blend

ZK = "227caea8b0ebc716f9fc1e55416070dc3dc59295142f95f9c933af0369d58c0a"
PROVIDER = "e48790bc24d4d8f32e162de8f0babc44eafffe313abec12a8422a3c1005382f8"


def _decl(created, active, ip="1.2.3.4", zk="aa", provider="bb", withdraw_at=None, service_type="BN"):
    return {
        "service_type": service_type, "provider_id": provider, "service_note_id": "note",
        "locators": [f"/ip4/{ip}/udp/3400/quic-v1"], "zk_id": zk,
        "created": created, "active": active, "withdraw_at": withdraw_at, "nonce": 0,
    }


def test_parse_locator():
    assert blend.parse_locator("/ip4/220.240.81.24/udp/3400/quic-v1") == ("220.240.81.24", 3400)
    assert blend.parse_locator("/dns4/node.example/udp/3400/quic-v1") == ("node.example", 3400)
    assert blend.parse_locator("garbage") == (None, None)


def test_classify():
    assert blend.classify(_decl(10, 14), 14) == "active"
    assert blend.classify(_decl(10, 13), 14) == "active"      # one epoch of lag allowed
    assert blend.classify(_decl(8, 10), 14) == "inactive"
    assert blend.classify(_decl(13, 13), 14) == "pending"     # activates at 15
    assert blend.classify(_decl(8, 14, withdraw_at=20), 14) == "withdrawn"


def test_summarize_network_counts_and_epoch():
    decls = {
        "d1": _decl(8, 14, ip="1.1.1.1"),
        "d2": _decl(8, 10, ip="2.2.2.2"),
        "d3": _decl(13, 13, ip="2.2.2.2"),
        "x": _decl(8, 14, service_type="DA"),                   # not Blend: ignored
    }
    s = blend.summarize(decls, None, None, None, None)
    assert s["epoch"] == 14
    assert s["network"] == {"core_nodes": 3, "distinct_hosts": 2,
                            "active": 1, "pending": 1, "inactive": 1, "withdrawn": 0}
    assert s["node"]["declared"] is False
    assert s["node"]["status"] == "not_declared"


def test_summarize_finds_our_declaration_by_zk_id_and_ip_match():
    decls = {"ours": _decl(13, 13, ip="220.240.81.24", zk=ZK), "other": _decl(8, 14)}
    s = blend.summarize(decls, {"core_info": None}, ZK, None, "220.240.81.24")
    node = s["node"]
    assert node["declared"] is True
    assert node["declaration_id"] == "ours"
    assert node["status"] == "pending"
    assert node["activates_at"] == 15
    assert node["registered_ip"] == "220.240.81.24"
    assert node["registered_port"] == 3400
    assert node["ip_match"] is True


def test_summarize_flags_ip_drift_matched_by_provider_id():
    decls = {"ours": _decl(8, 14, ip="220.240.81.24", provider=PROVIDER)}
    s = blend.summarize(decls, None, None, PROVIDER, "203.0.113.9")
    assert s["node"]["status"] == "active"
    assert s["node"]["ip_match"] is False


def test_summarize_ip_unknown_when_public_ip_missing():
    decls = {"ours": _decl(8, 14, zk=ZK)}
    s = blend.summarize(decls, None, ZK, None, None)
    assert s["node"]["ip_match"] is None


def test_our_blend_keys_reads_node_config_with_kms_tags(tmp_path):
    cfg = tmp_path / "user_config.yaml"
    cfg.write_text(
        "blend:\n"
        f"  non_ephemeral_signing_key_id: {PROVIDER}\n"
        "  core:\n"
        "    zk:\n"
        f"      secret_key_kms_id: {ZK}\n"
        "kms:\n"
        "  backend:\n"
        "    keys:\n"
        f"      {ZK}: !Zk 00ff\n"
    )
    assert blend.our_blend_keys(str(cfg)) == (ZK, PROVIDER)
    assert blend.our_blend_keys(str(tmp_path / "missing.yaml")) == (None, None)


def test_blend_status_endpoint():
    from dashboard.app import create_app

    app = create_app()
    app.config.update(TESTING=True, NODE_URL="http://node", BLEND_KEYS={"zk_id": ZK},
                      NODE_CONFIG_PATH=None)
    payloads = {
        "http://node/mantle/sdp/declarations": {"ours": _decl(13, 13, ip="220.240.81.24", zk=ZK)},
        "http://node/blend/info": {"node_id": "x", "core_info": None},
    }

    def fake_get(url, timeout):
        m = MagicMock(ok=True)
        m.json.return_value = payloads[url]
        return m

    with patch("requests.get", side_effect=fake_get), \
         patch("dashboard.blend.detect_public_ip", return_value="198.51.100.7"):
        resp = app.test_client().get("/api/blend/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["node"]["declaration_id"] == "ours"
    assert body["node"]["ip_match"] is False
    assert body["network"]["core_nodes"] == 1
