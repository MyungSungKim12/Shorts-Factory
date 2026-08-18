"""YouTube 공개 통계와 채널 소유자 Analytics 지표 수집."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


ANALYTICS_SCOPES = (
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)
ANALYTICS_TOKEN_PATH = Path("credentials/analytics_token.json")
CLIENT_SECRET_PATH = Path("credentials/client_secret.json")

OWNER_METRICS = (
    "engagedViews,views,estimatedMinutesWatched,averageViewDuration,"
    "averageViewPercentage,likes,comments,shares,subscribersGained,subscribersLost"
)


def load_analytics_credentials(
    *, token_path: Path = ANALYTICS_TOKEN_PATH
) -> Credentials:
    token_path = Path(token_path)
    if not token_path.is_file():
        raise FileNotFoundError(
            f"분석 인증이 필요합니다: {token_path} (analytics_token.json)"
        )
    credentials = Credentials.from_authorized_user_file(
        str(token_path), ANALYTICS_SCOPES
    )
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_path.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials.valid:
        raise RuntimeError("YouTube Analytics 읽기 토큰이 유효하지 않습니다")
    return credentials


def build_analytics_client(*, token_path: Path = ANALYTICS_TOKEN_PATH):
    return build(
        "youtubeAnalytics",
        "v2",
        credentials=load_analytics_credentials(token_path=token_path),
        cache_discovery=False,
    )


def _build_public_client():
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        raise RuntimeError("YOUTUBE_API_KEY가 설정되지 않았습니다")
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def normalize_report(response: dict) -> list[dict]:
    headers = [
        header.get("name")
        for header in response.get("columnHeaders", [])
        if isinstance(header, dict) and isinstance(header.get("name"), str)
    ]
    if not headers:
        return []
    result = []
    for row in response.get("rows", []) or []:
        if not isinstance(row, list):
            continue
        result.append(
            {
                header: row[index] if index < len(row) else None
                for index, header in enumerate(headers)
            }
        )
    return result


def _batches(values: Iterable[str], size: int) -> Iterable[list[str]]:
    batch = []
    for value in values:
        if not value:
            continue
        batch.append(str(value))
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def _integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_public_statistics(
    video_ids: Iterable[str],
    *,
    client=None,
) -> dict[str, dict]:
    youtube = client or _build_public_client()
    result: dict[str, dict] = {}
    for batch in _batches(video_ids, 50):
        response = youtube.videos().list(
            part="statistics", id=",".join(batch)
        ).execute()
        for item in response.get("items", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            stats = item.get("statistics") or {}
            result[str(item["id"])] = {
                "views": _integer(stats.get("viewCount")) or 0,
                "likes": _integer(stats.get("likeCount")) or 0,
                "comments": _integer(stats.get("commentCount")) or 0,
            }
    return result


def _owner_row(row: dict, end_date: date) -> dict:
    views = _integer(row.get("views"))
    engaged = _integer(row.get("engagedViews"))
    return {
        "engaged_views": engaged,
        "views": views,
        "engaged_view_rate": (
            round(engaged / views, 6)
            if engaged is not None and views is not None and views > 0
            else None
        ),
        "estimated_minutes_watched": _number(row.get("estimatedMinutesWatched")),
        "average_view_duration_sec": _number(row.get("averageViewDuration")),
        "average_view_percentage": _number(row.get("averageViewPercentage")),
        "likes": _integer(row.get("likes")),
        "comments": _integer(row.get("comments")),
        "shares": _integer(row.get("shares")),
        "subscribers_gained": _integer(row.get("subscribersGained")),
        "subscribers_lost": _integer(row.get("subscribersLost")),
        "analytics_end_date": end_date.isoformat(),
    }


def fetch_owner_metrics(
    video_ids: Iterable[str],
    start_date: date,
    end_date: date,
    *,
    client=None,
) -> dict[str, dict]:
    analytics = client or build_analytics_client()
    result: dict[str, dict] = {}
    for batch in _batches(video_ids, 500):
        response = analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date.isoformat(),
            endDate=end_date.isoformat(),
            dimensions="video",
            filters=f"video=={','.join(batch)}",
            metrics=OWNER_METRICS,
        ).execute()
        for row in normalize_report(response):
            video_id = row.get("video")
            if video_id:
                result[str(video_id)] = _owner_row(row, end_date)
    return result


def fetch_retention(
    video_id: str,
    start_date: date,
    end_date: date,
    *,
    client=None,
) -> list[dict]:
    analytics = client or build_analytics_client()
    response = analytics.reports().query(
        ids="channel==MINE",
        startDate=start_date.isoformat(),
        endDate=end_date.isoformat(),
        dimensions="elapsedVideoTimeRatio",
        filters=f"video=={video_id}",
        metrics="audienceWatchRatio,relativeRetentionPerformance",
    ).execute()
    return [
        {
            "elapsed_video_time_ratio": _number(row.get("elapsedVideoTimeRatio")),
            "audience_watch_ratio": _number(row.get("audienceWatchRatio")),
            "relative_retention_performance": _number(
                row.get("relativeRetentionPerformance")
            ),
        }
        for row in normalize_report(response)
        if _number(row.get("elapsedVideoTimeRatio")) is not None
    ]


def authorize_analytics(
    *,
    client_secret_path: Path = CLIENT_SECRET_PATH,
    token_path: Path = ANALYTICS_TOKEN_PATH,
    flow_factory: Callable = InstalledAppFlow.from_client_secrets_file,
    client_builder: Callable | None = None,
    today: date | None = None,
) -> dict:
    client_secret_path = Path(client_secret_path)
    token_path = Path(token_path)
    if not client_secret_path.is_file():
        raise FileNotFoundError(f"OAuth 클라이언트 파일이 없습니다: {client_secret_path}")
    flow = flow_factory(str(client_secret_path), ANALYTICS_SCOPES)
    credentials = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")

    analytics = (
        client_builder(credentials)
        if client_builder is not None
        else build(
            "youtubeAnalytics",
            "v2",
            credentials=credentials,
            cache_discovery=False,
        )
    )
    query_date = (today or date.today()) - timedelta(days=1)
    response = analytics.reports().query(
        ids="channel==MINE",
        startDate=query_date.isoformat(),
        endDate=query_date.isoformat(),
        metrics="views",
    ).execute()
    return {
        "token_path": str(token_path),
        "test_query_rows": len(response.get("rows", []) or []),
    }
