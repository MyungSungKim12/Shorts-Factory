"""업로드 당시 카테고리를 사용하는 성과 분석 회귀 테스트."""
import sqlite3

from app.agents import analyst


class _FakeRequest:
    def execute(self):
        return {
            "items": [
                {
                    "id": "science-video",
                    "statistics": {
                        "viewCount": "1200",
                        "likeCount": "12",
                        "commentCount": "1",
                    },
                },
                {
                    "id": "legacy-video",
                    "statistics": {
                        "viewCount": "900",
                        "likeCount": "4",
                        "commentCount": "0",
                    },
                },
            ]
        }


class _FakeVideos:
    def list(self, **kwargs):
        return _FakeRequest()


class _FakeYoutube:
    def videos(self):
        return _FakeVideos()


def test_analyst_uses_saved_category_and_separates_legacy_rows(
    tmp_path, monkeypatch
):
    db = sqlite3.connect(tmp_path / "videos.sqlite")
    db.execute(
        """
        CREATE TABLE videos (
            video_id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            title TEXT,
            topic TEXT,
            category TEXT,
            status TEXT NOT NULL,
            uploaded_at TEXT
        )
        """
    )
    db.executemany(
        "INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "science-video",
                "20260730-1",
                "과학 영상",
                "과학 소재",
                "science_mystery",
                "uploaded",
                "2026-07-30T11:00:00",
            ),
            (
                "legacy-video",
                "20260720-1",
                "과거 영상",
                "과거 소재",
                None,
                "uploaded",
                "2026-07-20T11:00:00",
            ),
        ],
    )
    db.commit()
    db.close()
    monkeypatch.setattr(
        analyst,
        "_youtube_readonly_client",
        lambda: _FakeYoutube(),
    )

    report = analyst.run_analyst(tmp_path)

    categories = {
        item["category"]: item for item in report["category_ranking"]
    }
    assert categories["과학의 경계/미해결 관측"]["avg_views"] == 1200
    assert categories["과거 미분류"]["avg_views"] == 900
    legacy = next(
        video
        for video in report["top_videos"]
        if video["video_id"] == "legacy-video"
    )
    assert legacy["category_key"] == "legacy_unclassified"
