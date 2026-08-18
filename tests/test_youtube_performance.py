from datetime import date

import pytest

from app.services import youtube_performance as performance


class FakeRequest:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class FakeVideos:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return FakeRequest(self.responses.pop(0))


class FakeDataClient:
    def __init__(self, responses):
        self.resource = FakeVideos(responses)

    def videos(self):
        return self.resource


class FakeReports:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return FakeRequest(self.responses.pop(0))


class FakeAnalyticsClient:
    def __init__(self, responses):
        self.resource = FakeReports(responses)

    def reports(self):
        return self.resource


def test_analytics_credentials_never_fall_back_to_upload_token(tmp_path):
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "token.json").write_text("upload-secret", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="analytics_token.json"):
        performance.load_analytics_credentials(
            token_path=credentials / "analytics_token.json"
        )

    assert performance.ANALYTICS_SCOPES == (
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    )


def test_normalize_report_uses_headers_instead_of_fixed_column_positions():
    response = {
        "columnHeaders": [
            {"name": "averageViewPercentage"},
            {"name": "video"},
            {"name": "engagedViews"},
            {"name": "views"},
        ],
        "rows": [[82.5, "v1", 800, 1000]],
    }

    assert performance.normalize_report(response) == [
        {
            "averageViewPercentage": 82.5,
            "video": "v1",
            "engagedViews": 800,
            "views": 1000,
        }
    ]
    assert performance.normalize_report({"columnHeaders": [], "rows": []}) == []


def test_public_statistics_batches_fifty_and_preserves_missing_counts():
    ids = [f"v{index}" for index in range(51)]
    client = FakeDataClient(
        [
            {
                "items": [
                    {
                        "id": "v0",
                        "statistics": {
                            "viewCount": "100",
                            "likeCount": "7",
                        },
                    }
                ]
            },
            {
                "items": [
                    {
                        "id": "v50",
                        "statistics": {
                            "viewCount": "22",
                            "commentCount": "1",
                        },
                    }
                ]
            },
        ]
    )

    result = performance.fetch_public_statistics(ids, client=client)

    assert result == {
        "v0": {"views": 100, "likes": 7, "comments": 0},
        "v50": {"views": 22, "likes": 0, "comments": 1},
    }
    assert len(client.resource.calls) == 2
    assert client.resource.calls[0]["part"] == "statistics"
    assert len(client.resource.calls[0]["id"].split(",")) == 50
    assert client.resource.calls[1]["id"] == "v50"


def test_owner_metrics_normalizes_names_and_computes_engaged_rate():
    client = FakeAnalyticsClient(
        [
            {
                "columnHeaders": [
                    {"name": "video"},
                    {"name": "engagedViews"},
                    {"name": "views"},
                    {"name": "estimatedMinutesWatched"},
                    {"name": "averageViewDuration"},
                    {"name": "averageViewPercentage"},
                    {"name": "likes"},
                    {"name": "comments"},
                    {"name": "shares"},
                    {"name": "subscribersGained"},
                    {"name": "subscribersLost"},
                ],
                "rows": [["v1", 800, 1000, 600.0, 45.0, 75.0, 12, 1, 3, 4, 0]],
            }
        ]
    )

    result = performance.fetch_owner_metrics(
        ["v1"], date(2026, 8, 1), date(2026, 8, 17), client=client
    )

    assert result["v1"] == {
        "engaged_views": 800,
        "views": 1000,
        "engaged_view_rate": 0.8,
        "estimated_minutes_watched": 600.0,
        "average_view_duration_sec": 45.0,
        "average_view_percentage": 75.0,
        "likes": 12,
        "comments": 1,
        "shares": 3,
        "subscribers_gained": 4,
        "subscribers_lost": 0,
        "analytics_end_date": "2026-08-17",
    }
    call = client.resource.calls[0]
    assert call["ids"] == "channel==MINE"
    assert call["dimensions"] == "video"
    assert call["filters"] == "video==v1"
    assert "engagedViews" in call["metrics"]


def test_retention_normalizes_empty_and_complete_reports():
    empty_client = FakeAnalyticsClient([{"columnHeaders": [], "rows": []}])
    assert performance.fetch_retention(
        "v1", date(2026, 8, 1), date(2026, 8, 17), client=empty_client
    ) == []

    client = FakeAnalyticsClient(
        [
            {
                "columnHeaders": [
                    {"name": "elapsedVideoTimeRatio"},
                    {"name": "audienceWatchRatio"},
                    {"name": "relativeRetentionPerformance"},
                ],
                "rows": [[0.0, 1.0, 0.2], [0.5, 0.7, -0.1]],
            }
        ]
    )
    assert performance.fetch_retention(
        "v1", date(2026, 8, 1), date(2026, 8, 17), client=client
    ) == [
        {
            "elapsed_video_time_ratio": 0.0,
            "audience_watch_ratio": 1.0,
            "relative_retention_performance": 0.2,
        },
        {
            "elapsed_video_time_ratio": 0.5,
            "audience_watch_ratio": 0.7,
            "relative_retention_performance": -0.1,
        },
    ]


def test_authorize_analytics_writes_only_the_dedicated_token(tmp_path):
    secret = tmp_path / "client_secret.json"
    secret.write_text("{}", encoding="utf-8")
    token = tmp_path / "analytics_token.json"
    calls = {}

    class FakeCredentials:
        def to_json(self):
            return '{"refresh_token":"analytics-only"}'

    class FakeFlow:
        def run_local_server(self, *, port):
            calls["port"] = port
            return FakeCredentials()

    def flow_factory(path, scopes):
        calls["secret"] = path
        calls["scopes"] = tuple(scopes)
        return FakeFlow()

    client = FakeAnalyticsClient(
        [{"columnHeaders": [{"name": "views"}], "rows": [[1]]}]
    )

    result = performance.authorize_analytics(
        client_secret_path=secret,
        token_path=token,
        flow_factory=flow_factory,
        client_builder=lambda credentials: client,
        today=date(2026, 8, 18),
    )

    assert result == {"token_path": str(token), "test_query_rows": 1}
    assert calls == {
        "secret": str(secret),
        "scopes": performance.ANALYTICS_SCOPES,
        "port": 0,
    }
    assert token.read_text(encoding="utf-8") == '{"refresh_token":"analytics-only"}'
    assert client.resource.calls[0]["ids"] == "channel==MINE"
