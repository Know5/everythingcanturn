"""表驱动的格式对测试。

加一个格式支持 → 在 CASES 里加一行。这张表同时是回归测试和能力文档。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from convcore import LossTier, convert_file

from .fidelity import exact, psnr_above, size_equals, ssim_above

# (样本 key, 目标格式, 期望损失等级, 校验器列表)
#
# 期望损失等级是断言而非注释：如果哪天改动让 PNG→BMP 变成有损，测试会失败。
CASES: list[tuple[str, str, LossTier, list]] = [
    # 无损互转：像素必须逐一相同
    ("png", "bmp", LossTier.LOSSLESS, [exact(), size_equals()]),
    ("png", "tiff", LossTier.LOSSLESS, [exact(), size_equals()]),
    ("bmp", "png", LossTier.LOSSLESS, [exact(), size_equals()]),
    ("tiff", "png", LossTier.LOSSLESS, [exact()]),
    ("png", "ppm", LossTier.LOSSLESS, [exact()]),
    # 有损压缩：不要求逐像素，但结构必须保住
    ("png", "jpg", LossTier.LOSSY, [size_equals(), ssim_above(0.95), psnr_above(30)]),
    ("png", "webp", LossTier.LOSSY, [size_equals(), ssim_above(0.95)]),
    ("jpg", "png", LossTier.LOSSLESS, [size_equals()]),
    ("webp", "png", LossTier.LOSSLESS, [size_equals()]),
    ("jpg", "webp", LossTier.LOSSY, [ssim_above(0.90)]),
    # 调色板格式：256 色量化，阈值要放宽
    ("png", "gif", LossTier.LOSSY, [size_equals(), ssim_above(0.80)]),
]


@pytest.mark.parametrize(
    "src_key,target,expect_loss,checks",
    CASES,
    ids=[f"{s}->{t}" for s, t, _, _ in CASES],
)
def test_conversion_matrix(samples, tmp_path, src_key, target, expect_loss, checks):
    src = samples.get(src_key)
    if src is None:
        pytest.skip(f"样本 {src_key} 不可用（缺可选依赖）")

    res = convert_file(src, target, out_dir=tmp_path, overwrite=True)

    assert len(res.outputs) == 1
    out = res.outputs[0]
    assert out.path is not None and out.path.exists()
    assert out.path.stat().st_size > 0

    assert res.loss is expect_loss, (
        f"损失等级不符：期望 {expect_loss.name}，实际 {res.loss.name}"
        f"（路径：{res.path_desc}）"
    )

    with Image.open(src) as a, Image.open(out.path) as b:
        for check in checks:
            check(a, b)


# ── 往返测试 ──
#
# A→B→A 后的退化说明 IR 丢了本该透传的信息，是设计缺陷而非误差。

ROUNDTRIPS = [
    ("png", "bmp", exact()),
    ("png", "tiff", exact()),
    ("png", "ppm", exact()),
    ("png", "webp", ssim_above(0.93)),
]


@pytest.mark.parametrize(
    "src_key,via,check", ROUNDTRIPS, ids=[f"{s}->{v}->{s}" for s, v, _ in ROUNDTRIPS]
)
def test_roundtrip(samples, tmp_path, src_key, via, check):
    src = samples.get(src_key)
    if src is None:
        pytest.skip(f"样本 {src_key} 不可用")

    mid = convert_file(src, via, out_dir=tmp_path / "a", overwrite=True).outputs[0].path
    back = convert_file(mid, src_key, out_dir=tmp_path / "b", overwrite=True).outputs[0].path

    with Image.open(src) as a, Image.open(back) as b:
        check(a, b)


# ── 降级策略 ──


def test_alpha_flattened_to_white_not_black(samples, tmp_path):
    """透明转 JPEG 必须合成到白底并告警。

    合成到黑底是最常见的踩坑：透明图标转完变成一团黑。
    """
    res = convert_file(samples["alpha_png"], "jpg", out_dir=tmp_path, overwrite=True)

    assert any("透明" in w for w in res.warnings), f"应有透明降级告警：{res.warnings}"

    with Image.open(res.outputs[0].path) as im:
        rgb = im.convert("RGB")
        # 四角原本全透明，应变成白色
        w, h = rgb.size
        for corner in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            r, g, b = rgb.getpixel(corner)
            assert min(r, g, b) > 240, f"角点 {corner} = {(r, g, b)}，应为白色"


def test_animation_preserved_when_target_supports_it(samples, tmp_path):
    """GIF→WebP 必须保住全部帧，不能退化成单帧。"""
    res = convert_file(samples["gif"], "webp", out_dir=tmp_path, overwrite=True)
    assert len(res.outputs) == 1

    with Image.open(res.outputs[0].path) as im:
        assert getattr(im, "n_frames", 1) > 1, "动图帧序列丢失"


def test_animation_becomes_multiple_files(samples, tmp_path):
    """GIF→PNG：目标不支持动画，默认导出为多文件而不是只留首帧。"""
    res = convert_file(samples["gif"], "png", out_dir=tmp_path, overwrite=True)

    assert len(res.outputs) > 1, "多帧应导出多个文件"
    assert any("动画" in w for w in res.warnings)
    names = [o.path.name for o in res.outputs]
    assert len(set(names)) == len(names), f"输出文件名重复：{names}"


def test_ico_packs_multiple_sizes(samples, tmp_path):
    """只存单尺寸的 ICO 在任务栏和大图标视图下会被拉伸得很糟。"""
    res = convert_file(samples["png"], "ico", out_dir=tmp_path, overwrite=True)
    with Image.open(res.outputs[0].path) as im:
        assert im.size[0] <= 256
        # Pillow 的 IcoFile.sizes 是方法而非属性
        packed = im.ico.sizes()
        assert len(packed) > 1, f"ICO 只打包了 {packed}，应有多个尺寸"
        assert (16, 16) in packed, f"缺少 16×16 层：{packed}"


def test_ico_pads_non_square_instead_of_stretching(samples, tmp_path):
    res = convert_file(samples["tall_png"], "ico", out_dir=tmp_path, overwrite=True)
    assert any("宽高比" in w for w in res.warnings)
    with Image.open(res.outputs[0].path) as im:
        assert im.size[0] == im.size[1], "ICO 必须是正方形"


# ── 几何与质量选项 ──


def test_max_edge_scales_proportionally(samples, tmp_path):
    res = convert_file(
        samples["png"], "png", out_dir=tmp_path, overwrite=True,
        overrides={"max_edge": 100},
    )
    with Image.open(samples["png"]) as src, Image.open(res.outputs[0].path) as out:
        assert max(out.size) == 100
        # 宽高比保持
        assert abs(out.size[0] / out.size[1] - src.size[0] / src.size[1]) < 0.02


def test_quality_affects_size(samples, tmp_path):
    lo = convert_file(
        samples["png"], "jpg", out_dir=tmp_path / "lo", overwrite=True,
        overrides={"quality": 40},
    ).outputs[0]
    hi = convert_file(
        samples["png"], "jpg", out_dir=tmp_path / "hi", overwrite=True,
        overrides={"quality": 95},
    ).outputs[0]
    assert lo.path.stat().st_size < hi.path.stat().st_size


def test_webp_lossless_is_exact(samples, tmp_path):
    res = convert_file(
        samples["png"], "webp", out_dir=tmp_path, overwrite=True,
        overrides={"lossless": True},
    )
    with Image.open(samples["png"]) as a, Image.open(res.outputs[0].path) as b:
        exact()(a, b)


def test_preset_web_limits_dimensions(samples, tmp_path):
    res = convert_file(
        samples["png"], "jpg", out_dir=tmp_path, overwrite=True, preset="web"
    )
    with Image.open(res.outputs[0].path) as im:
        assert max(im.size) <= 2048


# ── 元数据透传 ──


def test_icc_profile_preserved(tmp_path):
    """ICC 丢了颜色就会偏。这是「不透明扩展槽」必须存在的理由。"""
    src = tmp_path / "with_icc.png"
    im = Image.new("RGB", (32, 32), (120, 60, 200))
    fake_icc = b"\x00\x00\x02\x0cADBE" + b"\x00" * 500
    im.save(src, icc_profile=fake_icc)

    res = convert_file(src, "webp", out_dir=tmp_path / "out", overwrite=True)
    with Image.open(res.outputs[0].path) as out:
        assert out.info.get("icc_profile") == fake_icc


def test_same_format_with_options_actually_rewrites(samples, tmp_path):
    """PNG→PNG 带缩放选项时必须真的重写，不能当成无操作直接返回。

    回归测试：同格式短路会让所有选项被静默忽略——最难排查的一类 bug，
    因为命令成功退出、文件也存在，只是内容没变。
    """
    res = convert_file(
        samples["png"], "png", out_dir=tmp_path, overwrite=True,
        overrides={"max_edge": 120},
    )
    with Image.open(res.outputs[0].path) as im:
        assert max(im.size) == 120, "选项被忽略了"
    # 且路径不该绕远：READ→WRITE 两跳
    assert res.path_desc.count("→") == 2, f"路径绕远：{res.path_desc}"


def test_same_format_without_options_is_noop(samples, tmp_path):
    """无选项的同格式转换应当短路，不做无谓的编解码。"""
    res = convert_file(samples["png"], "png", out_dir=tmp_path, overwrite=True)
    assert res.loss is LossTier.COPY
    assert res.path_desc == "（无需转换）"


def test_refuses_to_overwrite_source(samples, tmp_path):
    """输出覆盖源文件会导致数据丢失，必须拒绝。"""
    import shutil

    from convcore import ConversionError

    local = tmp_path / "photo.png"
    shutil.copy(samples["png"], local)
    with pytest.raises(ConversionError, match="覆盖源文件"):
        convert_file(local, "png", out_path=local, overwrite=True,
                     overrides={"max_edge": 50})


def test_refuses_existing_target_without_overwrite(samples, tmp_path):
    from convcore import ConversionError

    convert_file(samples["png"], "bmp", out_dir=tmp_path, overwrite=True)
    with pytest.raises(ConversionError, match="已存在"):
        convert_file(samples["png"], "bmp", out_dir=tmp_path)


def test_strip_removes_metadata(tmp_path):
    src = tmp_path / "with_icc.png"
    im = Image.new("RGB", (32, 32), (10, 20, 30))
    im.save(src, icc_profile=b"\x00" * 128)

    res = convert_file(
        src, "jpg", out_dir=tmp_path / "out", overwrite=True,
        overrides={"keep_metadata": False, "keep_icc": False},
    )
    with Image.open(res.outputs[0].path) as out:
        assert not out.info.get("icc_profile")
