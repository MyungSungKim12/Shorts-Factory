# Monitor Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add server-side pagination and resilient list states to the uploaded-video and pipeline-run sections, then publish both repositories on their default `main` branches.

**Architecture:** FastAPI validates `page` and `page_size`, returns only one stable page plus shared pagination metadata, and filters active videos to `status='uploaded'`. React keeps independent page/loading/error state for videos and runs, renders a reusable pager, preserves the selected page during refresh, and corrects pages that become out of range.

**Tech Stack:** Python 3.12, FastAPI, SQLite, pytest, React 18, Vite 6, CSS, Oracle Linux deployment, Vercel deployment.

## Global Constraints

- Page defaults are `page=1`, `page_size=10`; bounds are `page>=1` and `1<=page_size<=50`.
- Existing response keys `videos` and `runs` remain backward compatible.
- Only `status='uploaded'` rows appear in the active video list.
- Automatic and manual refresh preserve each list's current page.
- The displayed schedule is `11:00·17:00·21:00` KST.
- Never commit or deploy `.env`, `credentials/`, `data/`, `.analysis_reference/`, or `.tmp_yt_dlp/`.
- Backend and frontend must both be pushed to `origin/main` after verification.

---

### Task 1: Paginated monitor API

**Files:**
- Modify: `app/main.py`
- Create: `tests/test_monitor_api.py`

**Interfaces:**
- Produces: `GET /api/videos?page:int&page_size:int -> {videos, pagination}`
- Produces: `GET /api/history?page:int&page_size:int -> {runs, pagination}`
- Produces: `_pagination(page: int, page_size: int, total_items: int) -> dict`

- [ ] **Step 1: Write failing video API tests**

Create temporary SQLite rows containing `uploaded` and `replaced` statuses. Assert page 2 returns the correct two uploaded records, excludes replaced rows, and returns:

```python
{
    "page": 2,
    "page_size": 2,
    "total_items": 5,
    "total_pages": 3,
    "has_previous": True,
    "has_next": True,
}
```

Also assert a missing DB returns `videos=[]` with zero totals and that `page=0`, `page_size=0`, and `page_size=51` return HTTP 422.

- [ ] **Step 2: Run video tests and verify RED**

Run: `python -m pytest tests/test_monitor_api.py -q`

Expected: failures because `/api/videos` ignores query parameters and omits `pagination`.

- [ ] **Step 3: Implement video pagination**

Use FastAPI `Query(1, ge=1)` and `Query(10, ge=1, le=50)`. Query count and rows with the same filter:

```sql
SELECT COUNT(*) FROM videos WHERE status = 'uploaded'
SELECT video_id, date, title, status, uploaded_at
FROM videos
WHERE status = 'uploaded'
ORDER BY uploaded_at DESC, video_id DESC
LIMIT ? OFFSET ?
```

Return `total_pages=0` for zero records and an empty list for an out-of-range page.

- [ ] **Step 4: Write failing history API tests**

Create valid and malformed `run-*.json` files. Assert valid documents are sorted by JSON `timestamp` and `date`, page boundaries are correct, malformed JSON is ignored, and invalid query values return 422.

- [ ] **Step 5: Run history tests and verify RED**

Run: `python -m pytest tests/test_monitor_api.py -q`

Expected: history assertions fail because the current endpoint slices filenames before parsing and has no pagination metadata.

- [ ] **Step 6: Implement history pagination**

Parse every `run-*.json`, ignore `JSONDecodeError` and `OSError`, sort valid objects descending with:

```python
key=lambda run: (str(run.get("timestamp", "")), str(run.get("date", "")))
```

Then slice using `(page - 1) * page_size` and return the shared pagination object.

- [ ] **Step 7: Verify backend and commit**

Run:

```powershell
python -m pytest tests\test_monitor_api.py -q
python -m pytest -q
git diff --check
```

Expected: monitor tests and full suite pass with no diff errors.

Commit only `app/main.py` and `tests/test_monitor_api.py` with `feat: paginate monitor APIs`.

---

### Task 2: Independent pagers and resilient refresh UI

**Files:**
- Create: `D:\ms\shorts-factory-fe\src\Pagination.jsx`
- Modify: `D:\ms\shorts-factory-fe\src\App.jsx`
- Modify: `D:\ms\shorts-factory-fe\src\index.css`

**Interfaces:**
- Consumes: backend `{items, pagination}` responses from Task 1.
- Produces: `<Pagination pagination onPageChange disabled />`.

- [ ] **Step 1: Add the reusable pagination component**

Render `처음`, `이전`, nearby page numbers, `다음`, and `마지막`. Disable boundary buttons and expose `aria-current="page"` on the selected page. When `total_pages` is zero, render only `총 0건`.

- [ ] **Step 2: Replace list loading with independent state**

Add separate `videoPage`, `historyPage`, items, pagination, loading, and error state. Fetch `/api/videos?page=${videoPage}&page_size=10` and `/api/history?page=${historyPage}&page_size=10`; reject non-2xx responses.

Use monotonically increasing request IDs in `useRef` so an older response cannot overwrite a newer page. Keep existing data on refresh failure and show the list-specific error.

- [ ] **Step 3: Preserve and correct pages during refresh**

Manual refresh and the 30-second timer request the current pages. If a successful response reports `total_pages > 0` and the current page is larger, move once to `total_pages`; if total pages is zero, keep page 1.

- [ ] **Step 4: Improve monitoring copy and list presentation**

Display `매일 11:00·17:00·21:00 자동 실행`, add a visible manual refresh button, show total uploaded videos instead of current-page length, and give loading/error/empty states distinct copy. Preserve pipeline cards, reports, and external links.

- [ ] **Step 5: Add responsive styling**

Wrap tables in `.table-scroll`, add `.pagination`, `.page-buttons`, `.refresh-button`, `.list-error`, and mobile rules so controls wrap below 640px while tables scroll horizontally.

- [ ] **Step 6: Build and inspect the frontend diff**

Run:

```powershell
npm run build
git diff --check
git status --short
```

Expected: Vite build succeeds and only the three intended frontend files are modified/created.

Commit with `feat: paginate monitor dashboard`.

---

### Task 3: Deploy and verify the complete monitor flow

**Files:**
- Deploy backend source/tests from Task 1 only.
- No credential or data files are transferred.

**Interfaces:**
- Consumes: local verified backend and frontend commits.
- Produces: live paginated API responses and a Vercel build from frontend `main`.

- [ ] **Step 1: Back up and deploy backend API files**

Back up server `app/main.py` and any existing monitor tests under `/home/ubuntu/backups/monitor-pagination-<timestamp>/`, then copy the new files. Restart the API service only if source reload is not active.

- [ ] **Step 2: Verify server tests and live API contracts**

Run the full server pytest suite. Query page 1 and an out-of-range page for both endpoints; assert list length does not exceed 10 and `pagination.total_items`, `total_pages`, `has_previous`, and `has_next` are coherent.

- [ ] **Step 3: Visually verify the frontend**

Run a local or deployed build against the live API. Confirm video and history pages move independently, refresh preserves each page, the schedule is correct, errors do not erase existing rows, and mobile controls remain usable.

- [ ] **Step 4: Merge backend to default branch**

Fetch `origin`, confirm `origin/main` has not diverged unexpectedly, switch the primary backend worktree to `main`, merge `codex/free-story-shorts-implementation` without discarding unrelated local files, rerun the full suite, and push `main`.

- [ ] **Step 5: Push frontend default branch**

Confirm the frontend worktree is clean except for the committed monitor changes, pull/fetch safely, and push frontend `main` to trigger Vercel.

- [ ] **Step 6: Final verification**

Confirm both `origin/main` refs contain the new commits, local/server backend tests pass, frontend build passes, live API pagination works, and no sensitive or temporary files are tracked.
