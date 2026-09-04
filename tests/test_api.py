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
