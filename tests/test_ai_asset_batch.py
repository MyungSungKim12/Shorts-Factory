import json


def test_build_jobs_interleaves_image_and_video_variants():
    from app.services.ai_asset_batch import SubjectSeed, build_jobs

    jobs = build_jobs(
        [SubjectSeed("door-to-hell", "다르바자 가스 분화구", "Darvaza gas crater", "지형")],
        video_variants=2,
        image_estimate_krw=56,
        video_estimate_krw=560,
    )

    assert [(job.kind, job.variant, job.estimated_krw) for job in jobs] == [
        ("image", 1, 56),
        ("video", 1, 560),
        ("video", 2, 560),
    ]


def test_pending_jobs_excludes_completed_work_after_restart(tmp_path):
    from app.services.ai_asset_batch import (
        SubjectSeed,
        build_jobs,
        load_or_create_state,
        pending_jobs,
        record_job_result,
    )

    state_path = tmp_path / "state.json"
    state = load_or_create_state(
        state_path,
        budget_krw=250_000,
        committed_start_krw=120_000,
    )
    jobs = build_jobs(
        [SubjectSeed("richat", "리차트 구조", "Richat Structure", "지형")],
        video_variants=2,
        image_estimate_krw=56,
        video_estimate_krw=560,
    )
    record_job_result(state_path, state, jobs[0], status="completed", detail={"path": "x.jpg"})

    resumed = load_or_create_state(
        state_path,
        budget_krw=999_999,
        committed_start_krw=999_999,
    )

    assert resumed["budget_krw"] == 250_000
    assert resumed["committed_start_krw"] == 120_000
    assert [job.job_id for job in pending_jobs(jobs, resumed)] == [
        "richat:video:1",
        "richat:video:2",
    ]
    assert json.loads(state_path.read_text(encoding="utf-8"))["jobs"]["richat:image:1"]["status"] == "completed"


def test_stop_reason_blocks_next_call_before_budget_is_crossed():
    from app.services.ai_asset_batch import stop_reason

    assert stop_reason(
        batch_spent_krw=249_500,
        next_estimated_krw=560,
        budget_krw=250_000,
        free_bytes=20 * 1024**3,
        min_free_bytes=12 * 1024**3,
        credit_mode="premium",
    ) == "batch_budget_limit"


def test_stop_reason_blocks_when_disk_reserve_is_reached():
    from app.services.ai_asset_batch import stop_reason

    assert stop_reason(
        batch_spent_krw=1_000,
        next_estimated_krw=560,
        budget_krw=250_000,
        free_bytes=11 * 1024**3,
        min_free_bytes=12 * 1024**3,
        credit_mode="premium",
    ) == "disk_reserve_limit"


def test_failed_jobs_are_retryable_but_rejected_outputs_are_not(tmp_path):
    from app.services.ai_asset_batch import (
        SubjectSeed,
        build_jobs,
        load_or_create_state,
        pending_jobs,
        record_job_result,
    )

    state_path = tmp_path / "state.json"
    state = load_or_create_state(state_path, budget_krw=250_000, committed_start_krw=0)
    jobs = build_jobs(
        [SubjectSeed("fairy-circles", "나미브 요정의 원", "Namib fairy circles", "자연")],
        video_variants=1,
        image_estimate_krw=56,
        video_estimate_krw=560,
    )
    record_job_result(state_path, state, jobs[0], status="failed", detail={"error": "quota"})
    record_job_result(state_path, state, jobs[1], status="rejected", detail={"error": "visual mismatch"})

    assert [job.job_id for job in pending_jobs(jobs, state)] == [
        "fairy-circles:image:1"
    ]


def test_load_subject_seeds_rejects_duplicate_keys(tmp_path):
    from app.services.ai_asset_batch import load_subject_seeds

    source = tmp_path / "subjects.json"
    source.write_text(json.dumps([
        {"key": "richat", "title_ko": "리차트", "exact_query": "Richat Structure", "category": "지형"},
        {"key": "richat", "title_ko": "리차트2", "exact_query": "Eye of Sahara", "category": "지형"},
    ], ensure_ascii=False), encoding="utf-8")

    try:
        load_subject_seeds(source)
    except ValueError as exc:
        assert "duplicate subject key" in str(exc)
    else:
        raise AssertionError("duplicate subject keys must be rejected")


def test_run_batch_records_completed_job_and_stops_before_overspend(tmp_path):
    from app.services.ai_asset_batch import (
        SubjectSeed,
        build_jobs,
        load_or_create_state,
        run_batch,
    )

    state_path = tmp_path / "state.json"
    state = load_or_create_state(state_path, budget_krw=600, committed_start_krw=1000)
    jobs = build_jobs(
        [SubjectSeed("richat", "리차트", "Richat Structure", "지형")],
        video_variants=1,
        image_estimate_krw=56,
        video_estimate_krw=560,
    )
    calls = []
    committed = iter([1000, 1056, 1056])

    result = run_batch(
        jobs,
        state_path=state_path,
        state=state,
        status_provider=lambda: {
            "committed_krw": next(committed),
            "mode": "premium",
            "free_bytes": 20 * 1024**3,
        },
        executor=lambda job: calls.append(job.job_id) or ("completed", {"ok": True}),
        min_free_bytes=12 * 1024**3,
    )

    assert calls == ["richat:image:1"]
    assert result["stop_reason"] == "batch_budget_limit"
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["jobs"]["richat:image:1"]["status"] == "completed"
