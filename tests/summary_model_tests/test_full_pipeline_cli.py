from __future__ import annotations

import json

from summary_model import full_pipeline_cli
from summary_model.checks.models import ProcurementChecksReport
from summary_model.extraction_models import (
    ProcurementPackageExtraction,
    ScheduleApplicationSchema,
)
from summary_model.web_service import WebPipelineResult


def test_discovery_maps_known_pack_files_and_ignores_generated_report(tmp_path):
    names = [
        "0. Обращение о проведении закупки.docx",
        "1. ОНМЦК.docx",
        "1. ОНМЦК.pdf",
        "2. Описание объекта закупки_2.docx",
        "3. заявка_в_ПГ.docx",
        "4. Пояснительная_записка.docx",
        "5. Контракт_4.docx",
        "1. КП_1.pdf",
        "1. КП_2.pdf",
        "analysis_result (8).docx",
    ]
    for name in names:
        (tmp_path / name).write_bytes(b"fixture")

    selected, ignored = full_pipeline_cli.discover_uploaded_documents(tmp_path)

    assert [item["key"] for item in selected] == [
        "plan",
        "obrasheniye",
        "onmck",
        "ooz",
        "contract",
        "zapiska",
        "commercial_offer",
        "commercial_offer",
    ]
    assert any(item["name"] == "1. ОНМЦК.pdf" for item in ignored)
    assert any(item["name"] == "analysis_result (8).docx" for item in ignored)


def test_discovery_accepts_legacy_plan_and_onmck_file_names(tmp_path):
    (tmp_path / "1_Заявка_на_включение_в_план_график.docx").write_bytes(b"plan")
    (tmp_path / "2_ОЦК_метод_сопоставимых_рыночных_цен.docx").write_bytes(b"onmck")

    selected, ignored = full_pipeline_cli.discover_uploaded_documents(tmp_path)

    assert [item["key"] for item in selected] == ["plan", "onmck"]
    assert ignored == []


def test_full_pipeline_cli_uses_web_pipeline_and_writes_diagnostics(tmp_path, monkeypatch):
    input_dir = tmp_path / "pack"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "3. заявка_в_ПГ.docx").write_bytes(b"plan")
    (input_dir / "1. КП_1.pdf").write_bytes(b"offer")
    captured = {}
    package = ProcurementPackageExtraction(
        package_id="package-test",
        schedule_application=ScheduleApplicationSchema(
            document_title="Заявка",
            purchase_subject="Поставка оборудования",
        ),
    )
    checks = ProcurementChecksReport.from_results(package_id="package-test", results=[])

    def fake_process(documents, *, options):
        captured["documents"] = documents
        captured["options"] = options
        return WebPipelineResult(
            report_text="report",
            package_id="package-test",
            warnings=["warning"],
            metrics={"document_llm": {"calls": 1}},
            package=package,
            checks_report=checks,
        )

    monkeypatch.setattr(full_pipeline_cli, "process_uploaded_documents", fake_process)

    exit_code = full_pipeline_cli.main(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert [item["key"] for item in captured["documents"]] == [
        "plan",
        "commercial_offer",
    ]
    assert captured["options"].with_vlm_tables is True
    assert json.loads((output_dir / "run.json").read_text(encoding="utf-8"))["status"] == "completed"
    assert json.loads(
        (output_dir / "extraction_result.final.json").read_text(encoding="utf-8")
    )["schedule_application"]["purchase_subject"] == "Поставка оборудования"
    assert (output_dir / "checks.json").is_file()
    assert "warning" in (output_dir / "report_with_warnings.txt").read_text(encoding="utf-8")
