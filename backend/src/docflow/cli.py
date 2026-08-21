from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from docflow.db.models import Base, IngestionJob
from docflow.db.session import SessionLocal, engine
from docflow.domain.jobs import IngestionJobCreate, IngestionOptions
from docflow.services.config_service import ensure_default_config
from docflow.services.expansion_sample import prepare_expansion_sample
from docflow.services.golden import select_golden_set
from docflow.services.inventory import inventory_job, write_inventory_report
from docflow.services.jobs import create_job
from docflow.services.pipeline import process_job

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


if __name__ == "__main__":
    app()
