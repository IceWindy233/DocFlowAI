"""公文文体度量。

把撰写与审核提示词里的定性风格约束变成可量化对照项：句长、并列密度、
力度词配额、标点纪律、占位符纪律。参数区间由 `docflow style-baseline`
从本地语料离线统计，写入运行配置的 `writing_style.baselines`。

设计取舍：区间偏离只作提示，不作错误判定。真实公文的个体离散度极大，
把中位数当合格线会把合规稿件判成问题稿件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from docflow.domain.config import GenreStyleBaseline, StyleMetric, StyleRange

MANDATORY_WORDS = ("应当", "必须", "不得", "严禁")
REQUIREMENT_WORDS = ("要", "切实", "确保", "务必")
SUGGESTION_WORDS = ("建议", "可以", "鼓励", "支持")

# 规范占位符：缺失事实一律占位，不得杜撰。
PLACEHOLDER_PATTERNS = {
    "PENDING_VALUE": re.compile(r"【待补：[^】]*】"),
    "ILLUSTRATIVE": re.compile(r"【示意·待核】"),
    "PENDING_SOURCE": re.compile(r"【待核对原文】"),
}
ANY_BRACKET_PATTERN = re.compile(r"【[^】]*】")
# 未补齐即不得定稿的两类：一类是缺数字，一类是给了参考量级待核。
BLOCKING_PLACEHOLDER_KINDS = ("PENDING_VALUE", "ILLUSTRATIVE")

HEADING1_PATTERN = re.compile(r"(?m)^\s*\**([一二三四五六七八九十]{1,3})、")
HEADING2_PATTERN = re.compile(r"(?m)^\s*\**（([一二三四五六七八九十]{1,3})）")
HEADING3_PATTERN = re.compile(r"(?m)^\s*\**(\d{1,2})[．.]\s*\D")
SENTENCE_SPLIT_PATTERN = re.compile(r"[。！？；]")
QUOTED_PATTERN = re.compile(r"[“][^”]{0,40}[”]")
PERCENT_PATTERN = re.compile(r"\d+(?:\.\d+)?%")
COLON_CONTEXT_PATTERN = re.compile(r"([^\n]{0,14})：")
# 冒号的三种合法位置：引语引入、主送机关、层次标题。
COLON_QUOTE_PATTERN = re.compile(r"说|问|讲|提|指出|反映|回忆|告诉|表示|答|如下|批复|函复|通知")
COLON_ORG_PATTERN = re.compile(r"人民政府|党委|党组|部门|单位|机关|公司|集团|局|中心|办公室|同志")
COLON_HEADING_PATTERN = re.compile(
    r"[一二三四五六七八九十]{1,3}、|（[一二三四五六七八九十]{1,3}）|\d{1,2}[．.]"
)


@dataclass(frozen=True)
class StyleDeviation:
    metric: StyleMetric
    value: float
    direction: str  # LOW | HIGH
    p25: float
    median: float
    p75: float


@dataclass(frozen=True)
class StyleMeasurement:
    metrics: dict[StyleMetric, float]
    placeholders: dict[str, list[str]] = field(default_factory=dict)
    unknown_placeholders: list[str] = field(default_factory=list)
    non_quote_colons: list[str] = field(default_factory=list)
    meta_comments: list[str] = field(default_factory=list)
    bad_phrases: list[tuple[str, str]] = field(default_factory=list)
    ascii_quotes: int = 0
    longest_line: tuple[int, str] = (0, "")

    @property
    def blocking_placeholders(self) -> list[str]:
        values: list[str] = []
        for kind in BLOCKING_PLACEHOLDER_KINDS:
            values.extend(self.placeholders.get(kind, []))
        return values

    def to_payload(self) -> dict[str, Any]:
        return {
            "metrics": {metric.value: value for metric, value in self.metrics.items()},
            "placeholders": dict(self.placeholders),
            "unknown_placeholders": list(self.unknown_placeholders),
            "non_quote_colons": list(self.non_quote_colons),
            "meta_comments": list(self.meta_comments),
            "bad_phrases": [
                {"phrase": phrase, "suggestion": suggestion}
                for phrase, suggestion in self.bad_phrases
            ],
            "ascii_quotes": self.ascii_quotes,
            "longest_line_length": self.longest_line[0],
        }


def _plain_body(text: str) -> tuple[str, list[str]]:
    """剥掉 Markdown 装饰，保留正文行；表格与引用块不参与文体度量。"""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^(\||-{3,}|>)", line):
            continue
        lines.append(re.sub(r"^#{1,6}\s*", "", line))
    body = "\n".join(lines)
    return re.sub(r"\*\*|__|`", "", body), lines


def _non_quote_colons(plain: str) -> list[str]:
    values: list[str] = []
    for match in COLON_CONTEXT_PATTERN.finditer(plain):
        before = match.group(1).strip()
        if not before:
            continue
        if COLON_HEADING_PATTERN.search(before):
            continue
        if COLON_QUOTE_PATTERN.search(before):
            continue
        if COLON_ORG_PATTERN.search(before):
            continue
        if "待补" in before or "待核" in before:
            continue
        values.append(match.group(0).strip()[:30])
    return values


def measure_style(
    text: str,
    *,
    bad_phrases: dict[str, str] | None = None,
    meta_comment_words: list[str] | None = None,
) -> StyleMeasurement:
    """量出正文的文体特征。纯函数，不做任何判定。"""
    plain, lines = _plain_body(text)
    body = plain.replace("\n", "")
    chars = len(body)
    if not chars:
        return StyleMeasurement(metrics={metric: 0.0 for metric in StyleMetric})

    sentences = [
        value.strip()
        for value in SENTENCE_SPLIT_PATTERN.split(plain)
        if len(value.strip()) >= 4
    ]
    sentence_length = (
        round(sum(len(value) for value in sentences) / len(sentences), 1) if sentences else 0.0
    )
    longest = max(((len(value), value) for value in lines), default=(0, ""))

    # 力度词统计剔除引号内内容：自造概念常带力度字样，计入会顶破配额。
    depunct = QUOTED_PATTERN.sub("", plain)
    # “不得不”是“只能”义，不是禁止义。
    mandatory_source = depunct.replace("不得不", "＃＃＃")
    # 占位符里的百分号不是真实数据。
    percent_source = ANY_BRACKET_PATTERN.sub("", plain)

    metrics: dict[StyleMetric, float] = {
        StyleMetric.CHARS: float(chars),
        StyleMetric.SENTENCE_LENGTH: sentence_length,
        StyleMetric.MAX_LINE_LENGTH: float(longest[0]),
        StyleMetric.DUN_PER_MILLE: round(1000 * body.count("、") / chars, 1),
        StyleMetric.MANDATORY_WORDS: float(
            sum(mandatory_source.count(word) for word in MANDATORY_WORDS)
        ),
        StyleMetric.REQUIREMENT_WORDS: float(
            sum(depunct.count(word) for word in REQUIREMENT_WORDS)
        ),
        StyleMetric.SUGGESTION_WORDS: float(
            sum(depunct.count(word) for word in SUGGESTION_WORDS)
        ),
        StyleMetric.PERCENT_VALUES: float(len(PERCENT_PATTERN.findall(percent_source))),
        StyleMetric.DASHES: float(body.count("——")),
        StyleMetric.HEADING_LEVEL1: float(len(HEADING1_PATTERN.findall(plain))),
        StyleMetric.HEADING_LEVEL2: float(len(HEADING2_PATTERN.findall(plain))),
        StyleMetric.HEADING_LEVEL3: float(len(HEADING3_PATTERN.findall(plain))),
    }

    placeholders = {
        kind: pattern.findall(plain)
        for kind, pattern in PLACEHOLDER_PATTERNS.items()
        if pattern.search(plain)
    }
    known = {value for values in placeholders.values() for value in values}
    unknown = [value for value in ANY_BRACKET_PATTERN.findall(plain) if value not in known]

    phrases = bad_phrases if bad_phrases is not None else {}
    meta_words = meta_comment_words if meta_comment_words is not None else []
    meta_source = QUOTED_PATTERN.sub("", plain)

    return StyleMeasurement(
        metrics=metrics,
        placeholders=placeholders,
        unknown_placeholders=unknown,
        non_quote_colons=_non_quote_colons(plain),
        meta_comments=[word for word in meta_words if word in meta_source],
        bad_phrases=[
            (phrase, suggestion) for phrase, suggestion in phrases.items() if phrase in plain
        ],
        ascii_quotes=body.count('"'),
        longest_line=longest,
    )


def compare_to_baseline(
    measurement: StyleMeasurement,
    baseline: GenreStyleBaseline,
) -> list[StyleDeviation]:
    """返回落在 p25-p75 之外的指标。方向比数值更有用：多项同向偏离才说明节奏不对。"""
    deviations: list[StyleDeviation] = []
    for metric, style_range in baseline.metrics.items():
        if metric not in measurement.metrics:
            continue
        value = measurement.metrics[metric]
        if value < style_range.p25:
            direction = "LOW"
        elif value > style_range.p75:
            direction = "HIGH"
        else:
            continue
        deviations.append(
            StyleDeviation(
                metric=metric,
                value=value,
                direction=direction,
                p25=style_range.p25,
                median=style_range.median,
                p75=style_range.p75,
            )
        )
    return deviations


def style_report(
    text: str,
    baseline: GenreStyleBaseline | None,
    *,
    bad_phrases: dict[str, str] | None = None,
    meta_comment_words: list[str] | None = None,
) -> dict[str, Any]:
    """撰写侧的文体对照报告。始终是提示，不参与 passed 判定。"""
    measurement = measure_style(
        text, bad_phrases=bad_phrases, meta_comment_words=meta_comment_words
    )
    payload = measurement.to_payload()
    if baseline is None:
        payload["baseline_available"] = False
        payload["deviations"] = []
        return payload
    payload["baseline_available"] = True
    payload["baseline_sample_size"] = baseline.sample_size
    payload["baseline_source"] = baseline.source_label
    payload["deviations"] = [
        {
            "metric": item.metric.value,
            "value": item.value,
            "direction": item.direction,
            "p25": item.p25,
            "median": item.median,
            "p75": item.p75,
        }
        for item in compare_to_baseline(measurement, baseline)
    ]
    return payload


def quantiles(values: list[float]) -> StyleRange:
    """线性插值四分位，与 `docflow style-baseline` 的输出保持一致。"""
    if not values:
        return StyleRange(p25=0.0, median=0.0, p75=0.0)
    ordered = sorted(values)

    def pick(ratio: float) -> float:
        position = (len(ordered) - 1) * ratio
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 1)

    return StyleRange(p25=pick(0.25), median=pick(0.5), p75=pick(0.75))
