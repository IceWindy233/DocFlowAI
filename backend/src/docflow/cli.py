from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from docflow.db.models import Base, IngestionJob
from docflow.db.session import SessionLocal, engine
from docflow.domain.config import RuntimeConfigBundleV1, StyleMetric
from docflow.domain.jobs import IngestionJobCreate, IngestionOptions
from docflow.services.config_service import ensure_default_config, get_current_config, save_config
from docflow.services.expansion_sample import prepare_expansion_sample
from docflow.services.golden import select_golden_set
from docflow.services.inventory import inventory_job, write_inventory_report
from docflow.services.jobs import create_job
from docflow.services.pipeline import process_job
from docflow.services.style_baseline import (
    DEFAULT_PATTERNS,
    build_baseline,
    collect_documents,
    extract_text,
)

app = typer.Typer(help="DocFlow AI M0-M1 管理命令")
console = Console()


@app.command("init-db")
def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        version = ensure_default_config(db)
    console.print(f"数据库初始化完成，当前配置 v{version.version} ({version.id})")


@app.command("scan")
def scan(
    source_root: Path = typer.Option(Path(".."), exists=True, file_okay=False),
    replace: bool = False,
) -> None:
    """执行 M0 全量盘点，不解析正文、不调用云端模型。"""
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        ensure_default_config(db)
        job = create_job(
            db,
            IngestionJobCreate(
                job_type="FULL_SCAN",
                source_root=str(source_root),
                options=IngestionOptions(inventory_only=True),
            ),
        )
        report = inventory_job(db, job, replace_existing=replace)
        output = write_inventory_report(report)
        job.status = "SUCCEEDED"
        db.commit()
    table = Table(title="DocFlow M0 盘点结果")
    table.add_column("状态")
    table.add_column("数量", justify="right")
    for status, count in report["summary"]["status_counts"].items():
        table.add_row(status, str(count))
    console.print(table)
    console.print(f"报告：{output}")


@app.command("golden")
def golden(job_id: str, replace: bool = False) -> None:
    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        if not job:
            raise typer.BadParameter("任务不存在")
        report = select_golden_set(db, job, replace=replace)
    console.print_json(json.dumps(report, ensure_ascii=False))


@app.command("run")
def run(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        if not job:
            raise typer.BadParameter("任务不存在")
        result = process_job(db, job)
        console.print(f"任务 {result.id}: {result.status}")


@app.command("expansion-sample")
def expansion_sample(
    inventory_job_id: str = typer.Argument(..., help="覆盖全量语料的 M0 盘点任务 ID"),
    target: int = typer.Option(200, min=1, help="目标文件数"),
    output_root: Path = typer.Option(
        Path("../data/expansion-sets/expansion-200-v1"),
        help="独立测试数据源目录",
    ),
    report_path: Path = typer.Option(
        Path("../data/reports/expansion-sample-200-v1.json"),
        help="选样清单输出路径",
    ),
    seed: str = typer.Option("docflow-expansion-v1", help="确定性选样种子"),
    replace: bool = typer.Option(False, help="替换已有测试目录"),
) -> None:
    """从 M0 清单生成不含既有解析文件的分层扩展测试集。"""
    with SessionLocal() as db:
        report = prepare_expansion_sample(
            db,
            inventory_job_id=inventory_job_id,
            output_root=output_root,
            report_path=report_path,
            target=target,
            seed=seed,
            replace=replace,
        )
    selection = report["selection"]
    table = Table(title=f"DocFlow 扩展测试集（{selection['target']} 文件）")
    table.add_column("类别")
    table.add_column("数量", justify="right")
    for category, count in selection["category_counts"].items():
        table.add_row(category, str(count))
    console.print(table)
    console.print(f"数据源：{report['source_root']}")
    console.print(f"清单：{report_path.resolve()}")


@app.command("style-baseline")
def style_baseline(
    source_root: str = typer.Option(..., help="本地语料目录"),
    document_type: str = typer.Option(..., help="文种键：REQUEST 或 LETTER"),
    output: str = typer.Option("", help="输出 JSON 路径；缺省只打印"),
    apply: bool = typer.Option(False, help="直接写入当前运行配置的 writing_style.baselines"),
    pattern: list[str] = typer.Option(
        [], help="文件名通配，可重复；缺省统计 doc/docx/txt/md"
    ),
    exclude: list[str] = typer.Option(
        [], help="路径片段黑名单，可重复；用于剔除附件与非本文种材料"
    ),
) -> None:
    """统计本地语料的文体区间，写入撰写与审核共用的 writing_style.baselines。

    只输出 p25/中位/p75 与样本数，不保留任何原文片段。语料目录只留在本机，不应提交 Git。
    """
    genre = document_type.strip().upper()
    if genre not in {"REQUEST", "LETTER"}:
        raise typer.BadParameter("文种键只支持 REQUEST 或 LETTER")
    root = Path(source_root).expanduser()
    if not root.is_dir():
        raise typer.BadParameter(f"语料目录不存在：{root}")

    paths = collect_documents(
        root,
        patterns=tuple(pattern) if pattern else DEFAULT_PATTERNS,
        excludes=tuple(exclude),
    )
    texts: list[str] = []
    failed = 0
    for path in paths:
        text = extract_text(path)
        if text is None:
            failed += 1
            continue
        texts.append(text)
    baseline = build_baseline(texts, source_label="本地语料")
    if baseline is None:
        raise typer.BadParameter("有效样本为 0，请检查语料目录与文档转换工具")
    # 有效篇数要等篇幅过滤之后才确定，基线来源标签因此在聚合后补齐。
    baseline = baseline.model_copy(update={"source_label": f"本地语料 n={baseline.sample_size}"})

    table = Table(title=f"{genre} 文体基线（样本 {baseline.sample_size} 篇）")
    table.add_column("指标")
    table.add_column("p25", justify="right")
    table.add_column("中位", justify="right")
    table.add_column("p75", justify="right")
    for metric in StyleMetric:
        style_range = baseline.metrics.get(metric)
        if style_range is None:
            continue
        table.add_row(
            metric.value,
            f"{style_range.p25:g}",
            f"{style_range.median:g}",
            f"{style_range.p75:g}",
        )
    console.print(table)
    console.print(
        f"扫描 {len(paths)} 篇，有效 {baseline.sample_size} 篇，"
        f"跳过 {len(paths) - baseline.sample_size} 篇（其中转换失败 {failed} 篇）"
    )

    if output:
        output_path = Path(output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(baseline.model_dump(mode="json"), ensure_ascii=False, indent=2)
        output_path.write_text(payload, encoding="utf-8")
        console.print(f"基线 JSON：{output_path.resolve()}")

    if apply:
        with SessionLocal() as db:
            current = get_current_config(db)
            config = RuntimeConfigBundleV1.model_validate(current.content)
            config.writing_style.baselines[genre] = baseline
            version = save_config(
                db,
                base_version_id=current.id,
                config=config,
                change_reason=f"更新 {genre} 文种文体基线，样本 {baseline.sample_size} 篇",
                # 离线统计不动模型路由，云模型就绪探测会无谓拦住写入。
                enforce_model_readiness=False,
            )
        console.print(f"已写入运行配置 v{version.version}（{version.id}）")


if __name__ == "__main__":
    app()
