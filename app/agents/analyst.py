"""분석가 에이전트 — 업로드 영상의 성과를 수집해 카테고리별 비교 리포트 생성.

목적: "어느 카테고리(회차)가 조회수가 잘 나오는지"를 실측해, 소재 전략 개편의 근거를 준다.
수집: YouTube Data API v3 (조회수·좋아요·댓글). 재인증 불필요(youtube.upload 스코프로 조회 가능).
"""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from googleapiclient.discovery import build

from app.agents.researcher import SLOT_CATEGORIES


def _youtube_readonly_client():
    """공개 영상 통계 조회용 클라이언트 — OAuth 아닌 API 키 사용 (재인증 불필요).

    공개 영상의 조회수·좋아요는 공개 데이터라 YOUTUBE_API_KEY 하나로 읽힌다.
    (업로드용 OAuth 토큰은 upload 스코프만 있어 statistics 조회가 403난다.)
    """
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "YOUTUBE_API_KEY 미설정 — Google Cloud Console에서 API 키 발급 후 .env에 추가. "
            "(업로드 프로젝트에 이미 YouTube Data API v3가 켜져 있어 키만 만들면 됨)"
        )
    return build("youtube", "v3", developerKey=api_key)


def run_analyst(data_dir: Path) -> dict:
    """업로드된 영상들의 성과를 수집하고 카테고리별 리포트를 생성한다."""
    db_file = data_dir / "videos.sqlite"
    if not db_file.exists():
        return {"message": "업로드 기록 없음"}

    db = sqlite3.connect(db_file)
    try:
        # 성과 컬럼이 없으면 추가 (조회수 스냅샷 저장용)
        cols = [r[1] for r in db.execute("PRAGMA table_info(videos)")]
        for col in ("views", "likes", "comments", "stats_updated_at"):
            if col not in cols:
                db.execute(f"ALTER TABLE videos ADD COLUMN {col} INTEGER")
        db.commit()

        rows = db.execute(
            "SELECT video_id, date, title, topic FROM videos WHERE status = 'uploaded'"
        ).fetchall()
        if not rows:
            return {"message": "업로드된 영상 없음"}

        # 1. YouTube API로 통계 수집 (최대 50개씩 배치)
        youtube = _youtube_readonly_client()
        stats = {}
        video_ids = [r[0] for r in rows]
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            resp = youtube.videos().list(part="statistics", id=",".join(batch)).execute()
            for item in resp.get("items", []):
                s = item.get("statistics", {})
                stats[item["id"]] = {
                    "views": int(s.get("viewCount", 0)),
                    "likes": int(s.get("likeCount", 0)),
                    "comments": int(s.get("commentCount", 0)),
                }

        # 2. DB에 최신 통계 갱신
        now = datetime.now().isoformat()
        for vid, st in stats.items():
            db.execute(
                "UPDATE videos SET views=?, likes=?, comments=?, stats_updated_at=? WHERE video_id=?",
                (st["views"], st["likes"], st["comments"], now, vid),
            )
        db.commit()

        # 3. 카테고리(회차)별 집계
        cat_agg = {}
        per_video = []
        for vid, date, title, topic in rows:
            if vid not in stats:
                continue
            slot = _slot_of(date)
            cat = SLOT_CATEGORIES.get(slot, {}).get("name", "기타")
            st = stats[vid]
            per_video.append({
                "video_id": vid, "category": cat, "topic": topic or title,
                "views": st["views"], "likes": st["likes"], "comments": st["comments"],
                "url": f"https://youtube.com/shorts/{vid}",
            })
            a = cat_agg.setdefault(cat, {"count": 0, "views": 0, "likes": 0, "comments": 0})
            a["count"] += 1
            a["views"] += st["views"]
            a["likes"] += st["likes"]
            a["comments"] += st["comments"]

        # 카테고리별 평균 + 정렬
        cat_summary = []
        for cat, a in cat_agg.items():
            n = a["count"] or 1
            cat_summary.append({
                "category": cat,
                "videos": a["count"],
                "avg_views": round(a["views"] / n, 1),
                "total_views": a["views"],
                "avg_likes": round(a["likes"] / n, 1),
                "avg_comments": round(a["comments"] / n, 1),
            })
        cat_summary.sort(key=lambda x: x["avg_views"], reverse=True)

        # 상위 성과 영상 Top 5
        top_videos = sorted(per_video, key=lambda x: x["views"], reverse=True)[:5]

        report = {
            "generated_at": now,
            "total_videos": len(per_video),
            "category_ranking": cat_summary,   # 평균 조회수 내림차순
            "top_videos": top_videos,
            "insight": _make_insight(cat_summary),
        }

        # 4. 리포트 저장 (대시보드 /api/report가 읽음)
        report_dir = data_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "latest.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report
    finally:
        db.close()


def _slot_of(date: str) -> int | None:
    """'20260716-3' → 3. 회차 없으면 None."""
    if "-" in date:
        try:
            return int(date.rsplit("-", 1)[1])
        except ValueError:
            return None
    return None


def _make_insight(cat_summary: list) -> str:
    """사람이 읽을 한 줄 결론 — 표본 부족 시 판단 유보."""
    if not cat_summary:
        return "데이터 없음"
    total = sum(c["videos"] for c in cat_summary)
    if total < 8:
        return f"표본 {total}개로 부족 — 카테고리별 3개 이상 쌓인 뒤 판단 권장"
    best = cat_summary[0]
    worst = cat_summary[-1]
    if worst["avg_views"] > 0:
        ratio = best["avg_views"] / worst["avg_views"]
        return (f"'{best['category']}'가 평균 조회수 1위 (평균 {best['avg_views']:.0f}회), "
                f"'{worst['category']}'의 {ratio:.1f}배. 상위 카테고리 비중 확대 검토 가치 있음")
    return f"'{best['category']}'가 평균 조회수 1위 (평균 {best['avg_views']:.0f}회)"
