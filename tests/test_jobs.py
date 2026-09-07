"""任务队列测试：批量、失败隔离、取消。"""

from __future__ import annotations

from pathlib import Path

from convcore.jobs import Job, JobQueue, JobState


def test_batch_conversion(samples, tmp_path):
    q = JobQueue()
    q.add_files(
        [samples["png"], samples["alpha_png"], samples["tall_png"]],
        "webp",
        out_dir=tmp_path,
        overwrite=True,
    )
    q.run()

    assert q.summary().get(JobState.DONE) == 3
    assert len(list(tmp_path.glob("*.webp"))) == 3


def test_same_stem_different_formats_do_not_collide(samples, tmp_path):
    """转换混合格式文件夹时 photo.png 和 photo.bmp 都转 webp 会撞名。

    静默覆盖是真实的数据丢失，必须消歧。
    """
    q = JobQueue()
    q.add_files(
        [samples["png"], samples["bmp"], samples["tiff"]],
        "webp",
        out_dir=tmp_path,
        overwrite=True,
    )
    q.run()

    assert q.summary().get(JobState.DONE) == 3
    produced = sorted(p.name for p in tmp_path.glob("*.webp"))
    assert len(produced) == 3, f"产物被覆盖了，只剩：{produced}"


def test_one_failure_does_not_stop_others(samples, tmp_path):
    """失败隔离：一个坏文件不该让整批崩掉。"""
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"this is not an image")

    q = JobQueue()
    q.add_files([samples["png"], bad, samples["bmp"]], "jpg",
                out_dir=tmp_path / "out", overwrite=True)
    q.run()

    s = q.summary()
    assert s.get(JobState.DONE) == 2
    assert s.get(JobState.FAILED) == 1

    failed = [j for j in q if j.state is JobState.FAILED][0]
    assert failed.error  # 必须有可读的错误信息，不能是空的


def test_progress_reaches_one(samples, tmp_path):
    seen: list[float] = []
    q = JobQueue(listener=lambda j: seen.append(j.progress))
    q.add(Job(source=samples["png"], target="webp", out_dir=tmp_path, overwrite=True))
    q.run()

    assert seen and max(seen) == 1.0
    # 进度必须单调不减
    assert seen == sorted(seen), f"进度回退了：{seen}"


def test_cancel_before_run(samples, tmp_path):
    q = JobQueue()
    job = q.add(Job(source=samples["png"], target="webp", out_dir=tmp_path))
    job.cancel()
    q.run()
    assert job.state is JobState.CANCELLED
    assert not list(tmp_path.glob("*.webp"))
