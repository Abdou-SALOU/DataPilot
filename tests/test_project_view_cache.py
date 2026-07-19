import io

import app as datapilot_app

from test_app import client, csrf


def test_project_view_cache_is_invalidated_after_cleaning(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (
                io.BytesIO(b"ville,montant\nCasa,10\nCasa,10\nRabat,20\n"),
                "ventes.csv",
            ),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    project_id = upload.headers["Location"].rsplit("/", 1)[-1]

    first_view = datapilot_app.build_project_view(project_id)
    assert first_view["profile"]["rows"] == 3
    assert first_view["profile"]["duplicates"] == 1

    response = test_client.post(
        upload.headers["Location"] + "/clean",
        data={"_csrf": token, "actions": "drop_duplicates"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    refreshed_view = datapilot_app.build_project_view(project_id)
    assert refreshed_view["profile"]["rows"] == 2
    assert refreshed_view["profile"]["duplicates"] == 0
