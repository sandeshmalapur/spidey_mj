import os
import sqlite3
import pytest
from fastapi.testclient import TestClient

from app.config import APP_VERSION
from app.db import check_db_health, get_connection, init_db
from app.main import app


@pytest.fixture
def temp_db(tmp_path):
    test_db_path = str(tmp_path / "test_classmail.db")
    init_db(test_db_path)
    return test_db_path


def test_db_initialization(temp_db):
    conn = get_connection(temp_db)
    cursor = conn.cursor()

    # Verify sources table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sources';")
    assert cursor.fetchone() is not None

    cursor.execute("PRAGMA table_info(sources);")
    sources_cols = {row["name"] for row in cursor.fetchall()}
    assert {"id", "created_at", "name", "source_type"}.issubset(sources_cols)

    # Verify items table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='items';")
    assert cursor.fetchone() is not None

    cursor.execute("PRAGMA table_info(items);")
    items_cols = {row["name"] for row in cursor.fetchall()}
    expected_items_cols = {
        "id",
        "created_at",
        "title",
        "item_type",
        "kind",
        "summary",
        "priority",
        "priority_reason",
        "priority_override",
        "extraction_confidence",
        "state",
        "acknowledged_at",
        "snoozed_until",
        "deadline_text",
        "deadline_at",
        "deadline_status",
        "links",
        "source_type",
        "source_id",
        "source_title",
        "sender",
        "received_at",
        "evidence",
        "is_demo",
        "updated_at",
        "completed_at",
    }
    assert expected_items_cols.issubset(items_cols)

    conn.close()


def test_db_health_check(temp_db, tmp_path):
    # Healthy database returns True
    assert check_db_health(temp_db) is True

    # Missing database in non-existent directory returns False and does not create directory
    missing_dir = tmp_path / "missing_dir"
    missing_db_path = str(missing_dir / "db.db")
    assert check_db_health(missing_db_path) is False
    assert not missing_dir.exists()

    # Non-existent file in existing directory returns False and does not create file
    missing_file_path = str(tmp_path / "non_existent.db")
    assert check_db_health(missing_file_path) is False
    assert not (tmp_path / "non_existent.db").exists()


def test_api_health_endpoint():
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["db"] == "ok"
        assert data["version"] == APP_VERSION


def test_api_health_endpoint_error(monkeypatch):
    monkeypatch.setattr("app.main.check_db_health", lambda: False)
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 503
        data = response.json()
        assert data["detail"]["status"] == "error"
        assert data["detail"]["db"] == "error"
