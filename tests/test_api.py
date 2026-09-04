from fastapi.testclient import TestClient

from pin.node import create_app, get_lab


def test_http_quote_accept_receipt_roundtrip():
    get_lab.cache_clear()
    client = TestClient(create_app())
    caps = client.get("/pin/capabilities").json()
    assert caps["pin_version"] == "pin/1"
    assert caps["contracts_on_flop"] is False
    artifact_id = caps["artifacts"][0]["artifact_id"]

    quote = client.post(
        "/pin/quote",
        json={
            "artifact_id": artifact_id,
            "sla_class": "interactive",
            "tier": "T1",
            "n_in": 32,
            "n_out": 48,
        },
    ).json()
    assert quote["usd_micros"] > 0
    assert "flop_fee" in quote

    demo = client.post("/pin/demo", json={"attack": "", "artifact_key": "8b-stock"}).json()
    assert demo["status"] == "paid"
    job_id = demo["job_id"]
    receipt = client.get(f"/pin/receipt/{job_id}").json()
    assert receipt["paid"] is True
    assert receipt["usd_invoice_micros"] == demo["usd_invoice_micros"]

    swap = client.post(
        "/pin/demo", json={"attack": "model_swap", "artifact_key": "70b-stock"}
    ).json()
    assert swap["status"] == "fraud_slash"


def test_dashboard_served():
    client = TestClient(create_app())
    page = client.get("/")
    assert page.status_code == 200
    assert "Pinned Inference on Flop" in page.text


def test_fetch_only_agent_lanes():
    get_lab.cache_clear()
    client = TestClient(create_app())
    skill = client.get("/skill.md")
    assert skill.status_code == 200
    assert "pin1" in skill.text
    card = client.get("/.well-known/agent.json").json()
    assert card["conventions"]["tclk_rail"] == "flop-htlc"
    assert card["trust"]["contracts_on_flop"] is False
    caps = client.get("/pin/capabilities").json()
    assert caps["coordination"] == "technocore"
    assert caps["operator_did"].startswith("did:key:z6Mk")
    artifact_id = caps["artifacts"][0]["artifact_id"]
    quote = client.get(f"/g/quote/{artifact_id}/interactive/T1/32/48").json()
    assert quote["usd_micros"] > 0
    job = client.get("/g/agent-job/8b-stock").json()
    assert job["status"] == "paid"
    assert job["tclk_revealed"] is True
    assert job["frames"][0].startswith("pin1 ")
    room = client.get("/r/pin-jobs")
    assert room.status_code == 200
    assert "pin1 " in room.text
