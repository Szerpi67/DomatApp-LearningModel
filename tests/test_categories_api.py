import json

import api
from fastapi.testclient import TestClient


def test_categories_returns_filename_and_unique_topics_in_file_order(
    tmp_path, monkeypatch
):
    category = {
        "category_name": "Arytmetyka",
        "skills": [
            {"topic": "Liczby naturalne"},
            {"topic": "Działania"},
            {"topic": "Liczby naturalne"},
        ],
    }
    (tmp_path / "01-arytmetyka.json").write_text(
        json.dumps(category), encoding="utf-8"
    )
    monkeypatch.setattr(api, "SKILLS_DIRECTORY", tmp_path)

    response = TestClient(api.app).get("/api/v1/categories")

    assert response.status_code == 200
    assert response.json() == {
        "01-arytmetyka.json": {
            "id": "01-arytmetyka.json",
            "category_name": "Arytmetyka",
            "topics": ["Liczby naturalne", "Działania"],
        }
    }
