import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app


@pytest.fixture
def client_with_db(tmp_path, monkeypatch):
    test_db_path = str(tmp_path / "test_items.db")
    init_db(test_db_path)
    monkeypatch.setattr("app.config.DATABASE_PATH", test_db_path)
    with TestClient(app) as client:
        yield client


def test_item_crud_lifecycle(client_with_db):
    client = client_with_db

    # 1. Create item
    payload = {
        "title": "Submit Lab Report",
        "item_type": "task",
        "kind": "assignment",
        "summary": "Physics Lab 3 on optics",
        "priority": "important",
        "priority_reason": "Graded report",
        "state": "needs_review",
        "deadline_text": "Friday 5 PM",
        "deadline_at": "2026-11-20T22:00:00Z",
        "deadline_status": "confirmed",
        "links": ["https://physics.example/lab3"],
        "source_type": "manual",
        "source_id": "lab-report-3",
        "evidence": "Please turn in lab report by Friday 5 PM.",
    }
    create_res = client.post("/api/items", json=payload)
    assert create_res.status_code == 201
    item = create_res.json()
    item_id = item["id"]
    assert item["title"] == "Submit Lab Report"
    assert item["effective_priority"] == "important"
    assert item["state"] == "needs_review"
    assert item["links"] == ["https://physics.example/lab3"]

    # 2. Get item
    get_res = client.get(f"/api/items/{item_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == item_id

    # 3. Patch item
    patch_payload = {
        "title": "Submit Optics Lab Report Updated",
        "summary": "Updated instructions",
        "deadline_text": "Saturday noon",
        "deadline_status": "ambiguous",
        "priority_override": "urgent",
        "kind": "document_request",
        "item_type": "task",
    }
    patch_res = client.patch(f"/api/items/{item_id}", json=patch_payload)
    assert patch_res.status_code == 200
    updated_item = patch_res.json()
    assert updated_item["title"] == "Submit Optics Lab Report Updated"
    assert updated_item["summary"] == "Updated instructions"
    assert updated_item["priority_override"] == "urgent"
    assert updated_item["effective_priority"] == "urgent"
    assert updated_item["priority"] == "important"
    assert updated_item["deadline_status"] == "ambiguous"
    assert updated_item["kind"] == "document_request"

    # 4. 404 for non-existent item
    missing_res = client.get("/api/items/999999")
    assert missing_res.status_code == 404


def test_override_precedence_and_filters(client_with_db):
    client = client_with_db

    # Item A: priority normal, override urgent -> effective urgent
    res_a = client.post(
        "/api/items",
        json={
            "title": "Item A",
            "item_type": "task",
            "priority": "normal",
            "priority_override": "urgent",
            "state": "not_started",
        },
    )
    assert res_a.status_code == 201
    item_a = res_a.json()
    assert item_a["effective_priority"] == "urgent"

    # Item B: priority normal, no override -> effective normal
    res_b = client.post(
        "/api/items",
        json={
            "title": "Item B",
            "item_type": "information",
            "priority": "normal",
            "state": "needs_review",
        },
    )
    assert res_b.status_code == 201

    # Filter priority=urgent -> should return Item A only
    filter_urgent = client.get("/api/items?priority=urgent").json()
    ids_urgent = [i["id"] for i in filter_urgent]
    assert item_a["id"] in ids_urgent
    assert res_b.json()["id"] not in ids_urgent

    # Filter state=needs_review -> should return Item B only
    filter_state = client.get("/api/items?state=needs_review").json()
    ids_state = [i["id"] for i in filter_state]
    assert res_b.json()["id"] in ids_state
    assert item_a["id"] not in ids_state

    # Filter item_type=information -> should return Item B only
    filter_type = client.get("/api/items?item_type=information").json()
    ids_type = [i["id"] for i in filter_type]
    assert res_b.json()["id"] in ids_type
    assert item_a["id"] not in ids_state


def test_actions_lifecycle(client_with_db):
    client = client_with_db

    create_res = client.post(
        "/api/items",
        json={
            "title": "Action Test Task",
            "item_type": "task",
            "state": "needs_review",
        },
    )
    item_id = create_res.json()["id"]

    # 1. Acknowledge -> does not change state
    ack_res = client.post(f"/api/items/{item_id}/acknowledge")
    assert ack_res.status_code == 200
    ack_data = ack_res.json()
    assert ack_data["acknowledged_at"] is not None
    assert ack_data["state"] == "needs_review"

    # 2. Start -> changes state to in_progress
    start_res = client.post(f"/api/items/{item_id}/start")
    assert start_res.status_code == 200
    assert start_res.json()["state"] == "in_progress"

    # 3. Complete -> changes state to completed, sets completed_at
    comp_res = client.post(f"/api/items/{item_id}/complete")
    assert comp_res.status_code == 200
    comp_data = comp_res.json()
    assert comp_data["state"] == "completed"
    assert comp_data["completed_at"] is not None

    # 4. Reopen -> sets state to not_started, clears completed_at
    reopen_res = client.post(f"/api/items/{item_id}/reopen")
    assert reopen_res.status_code == 200
    reopen_data = reopen_res.json()
    assert reopen_data["state"] == "not_started"
    assert reopen_data["completed_at"] is None

    # 5. Cancel -> changes state to cancelled
    cancel_res = client.post(f"/api/items/{item_id}/cancel")
    assert cancel_res.status_code == 200
    assert cancel_res.json()["state"] == "cancelled"


def test_snooze_behavior(client_with_db):
    client = client_with_db

    create_res = client.post(
        "/api/items",
        json={
            "title": "Snooze Test Task",
            "item_type": "task",
            "state": "not_started",
        },
    )
    item_id = create_res.json()["id"]

    future_utc = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    snooze_res = client.post(f"/api/items/{item_id}/snooze", json={"until": future_utc})
    assert snooze_res.status_code == 200
    snooze_data = snooze_res.json()
    assert snooze_data["snoozed_until"] is not None
    assert snooze_data["state"] == "not_started"  # state unchanged

    # Default list excludes snoozed item
    list_res = client.get("/api/items").json()
    ids_default = [i["id"] for i in list_res]
    assert item_id not in ids_default

    # List with include_snoozed=true includes the item
    list_snoozed_res = client.get("/api/items?include_snoozed=true").json()
    ids_snoozed = [i["id"] for i in list_snoozed_res]
    assert item_id in ids_snoozed


def test_duplicate_source_returns_409(client_with_db):
    client = client_with_db

    payload = {
        "title": "Assignment from Classroom",
        "source_type": "classroom",
        "source_id": "course-123-assign-456",
    }

    first_res = client.post("/api/items", json=payload)
    assert first_res.status_code == 201

    duplicate_res = client.post("/api/items", json=payload)
    assert duplicate_res.status_code == 409


def test_demo_seed_and_delete(client_with_db):
    client = client_with_db

    # Create a non-demo manual item first
    manual_res = client.post(
        "/api/items",
        json={"title": "User Personal Item", "is_demo": False},
    )
    assert manual_res.status_code == 201
    manual_id = manual_res.json()["id"]

    # Seed demo items
    seed_res = client.post("/api/demo/seed")
    assert seed_res.status_code == 200
    seed_data = seed_res.json()
    assert seed_data["seeded"] == 7
    assert seed_data["total_demo"] == 7

    # Seed again -> idempotent (0 newly seeded)
    seed_again = client.post("/api/demo/seed")
    assert seed_again.status_code == 200
    assert seed_again.json()["seeded"] == 0
    assert seed_again.json()["total_demo"] == 7

    # Verify all 7 demo items are in list
    demo_list = client.get("/api/items?include_snoozed=true").json()
    assert len(demo_list) == 8  # 7 demo + 1 manual

    # Delete demo items -> ONLY is_demo rows removed
    del_res = client.delete("/api/demo")
    assert del_res.status_code == 200
    assert del_res.json()["deleted"] == 7

    # Verify manual item survived and demo items are gone
    remaining = client.get("/api/items?include_snoozed=true").json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == manual_id
    assert remaining[0]["title"] == "User Personal Item"
