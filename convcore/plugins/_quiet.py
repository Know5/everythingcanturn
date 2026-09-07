"""压制第三方库绕过 warnings 模块直写标准流的噪音。

pymupdf 和 pdf2docx 会打两类无关信息：
  1. fitz 别名的弃用警告（pdf2docx 的问题，不是我们的）——走 **stdout**
  2. "Consider using the pymupdf_layout package" 的功能推销

因为是直接写流而非经 warnings，filterwarnings 拦不住；又因为第 1 条落在
stdout 上，只包 stderr 也漏。quiet_output 同时管两条流。

关键原则：**不盲目丢弃输出。** 真实错误必须可见，否则出问题时无法诊断。
所以捕获后逐行过滤，只删匹配已知噪音的行，其余原样放回原本的流。
"""

from __future__ import annotations

import contextlib
import io
import re
import sys

_NOISE = re.compile(
    r"fitz.*deprecat|pymupdf_layout|Consider using the pymupdf",
    re.I,
)


def _flush_filtered(buf: io.StringIO, stream) -> None:
    for line in buf.getvalue().splitlines():
        if line.strip() and not _NOISE.search(line):
            print(line, file=stream)


@contextlib.contextmanager
def quiet_output():
    """捕获 stdout+stderr，过滤已知噪音后把其余内容放回各自的流。

    不合并两条流：stdout 是数据通道（CLI 的 --json 输出走这里），
    错误混进去会破坏下游解析。
    """
    out_buf, err_buf = io.StringIO(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_buf, err_buf
    try:
        yield
    finally:
        sys.stdout, sys.stderr = real_out, real_err
        _flush_filtered(out_buf, real_out)
        _flush_filtered(err_buf, real_err)


@contextlib.contextmanager
def quiet_stderr():
    """只管 stderr。保留给转换期调用——那时 stdout 可能正在输出结果数据。"""
    buf = io.StringIO()
    real = sys.stderr
    sys.stderr = buf
    try:
        yield
    finally:
        sys.stderr = real
        _flush_filtered(buf, real)
