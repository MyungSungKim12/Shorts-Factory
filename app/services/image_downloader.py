"""미디어 다운로드 서비스 (Pexels 무료 API — 이미지 + 비디오)."""
import os

import requests


async def download_image(keyword: str, output_path: str, api_key: str = None) -> str:
    """
    Pexels API로 세로형 이미지 다운로드 (폴백용).

    Args:
        keyword: 검색 키워드 (영어)
        output_path: 저장 경로
        api_key: Pexels API 키 (환경변수에서 읽음)

    Returns:
        다운로드된 파일 경로
    """
    if api_key is None:
        api_key = os.getenv("PEXELS_API_KEY")

    if not api_key:
        raise ValueError("PEXELS_API_KEY 환경변수가 없습니다")

    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": api_key}
    params = {
        "query": keyword,
        "per_page": 1,
        "orientation": "portrait",
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        photos = data.get("photos", [])

        if not photos:
            raise ValueError(f"검색 결과 없음: {keyword}")

        photo = photos[0]
        image_url = photo["src"]["original"]

        img_response = requests.get(image_url, timeout=10)
        img_response.raise_for_status()

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(img_response.content)

        return output_path

    except requests.RequestException as e:
        raise RuntimeError(f"이미지 다운로드 실패: {e}")


async def download_video(keyword: str, output_path: str, api_key: str = None) -> str:
    """
    Pexels API로 세로형 비디오 다운로드.

    Args:
        keyword: 검색 키워드 (영어)
        output_path: 저장 경로
        api_key: Pexels API 키 (환경변수에서 읽음)

    Returns:
        다운로드된 비디오 파일 경로
    """
    if api_key is None:
        api_key = os.getenv("PEXELS_API_KEY")

    if not api_key:
        raise ValueError("PEXELS_API_KEY 환경변수가 없습니다")

    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": api_key}
    params = {
        "query": keyword,
        "per_page": 1,
        "orientation": "portrait",
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        videos = data.get("videos", [])

        if not videos:
            # 첫 단어 재검색은 "afghan hound"→"afghan"(사람/풍경)처럼 엉뚱한 결과를 부르므로 금지.
            # 폴백은 호출부(_download_videos)가 카테고리 안전어로 명시 처리한다.
            raise ValueError(f"검색 결과 없음: {keyword}")

        video = videos[0]
        # 여러 해상도 중 portrait 선택
        video_files = video.get("video_files", [])

        # portrait 해상도 선택 (1080x1920 또는 가장 가까운 것)
        portrait_files = [f for f in video_files if f.get("width", 0) < f.get("height", 0)]
        if portrait_files:
            video_file = portrait_files[0]  # 첫 번째 portrait 파일
        else:
            video_file = video_files[0]  # 없으면 첫 번째

        video_url = video_file["link"]

        # 비디오 다운로드
        video_response = requests.get(video_url, timeout=30)
        video_response.raise_for_status()

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(video_response.content)

        return output_path

    except requests.RequestException as e:
        raise RuntimeError(f"비디오 다운로드 실패: {e}")


async def download_video_pixabay(keyword: str, output_path: str, api_key: str = None) -> str:
    """Pixabay API로 세로형 비디오 다운로드 (Pexels 폴백용)."""
    if api_key is None:
        api_key = os.getenv("PIXABAY_API_KEY")
    if not api_key:
        raise ValueError("PIXABAY_API_KEY 환경변수가 없습니다")

    try:
        response = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": api_key, "q": keyword, "per_page": 3},
            timeout=10,
        )
        response.raise_for_status()
        hits = response.json().get("hits", [])

        if not hits:
            raise ValueError(f"Pixabay 검색 결과 없음: {keyword}")

        # 세로형(height>width) 우선, 없으면 첫 결과. Pixabay는 large/medium/small 버전 제공
        def pick(hit):
            vids = hit.get("videos", {})
            return vids.get("large") or vids.get("medium") or vids.get("small")

        chosen = None
        for hit in hits:
            v = pick(hit)
            if v and v.get("height", 0) >= v.get("width", 0):
                chosen = v
                break
        if not chosen:
            chosen = pick(hits[0])
        if not chosen or not chosen.get("url"):
            raise ValueError("Pixabay 비디오 URL 없음")

        video_response = requests.get(chosen["url"], timeout=30)
        video_response.raise_for_status()

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(video_response.content)

        return output_path

    except requests.RequestException as e:
        raise RuntimeError(f"Pixabay 비디오 다운로드 실패: {e}")
