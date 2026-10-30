import io
import json
from pathlib import Path

import fitz
import pytest

from grader_core.grading import approved_scores, score_question, validate_findings
from grader_core.pdf_tools import annotated_pdf, check_regions, image_location_to_pdf, render_region
from grader_core.pipeline import analyze
from grader_core.schema import Box, Finding, QuestionResult, Rubric
from grader_core.storage import Store


@pytest.fixture()
def rubric():
    file = Path(__file__).resolve().parents[1] / "examples" / "rubric.json"
    return Rubric.model_validate_json(file.read_text())


@pytest.fixture()
def simple_pdf():
    doc = fitz.open()
    for num in range(2):
        page = doc.new_page(width=600, height=800)
        page.insert_text((70, 250), f"ANSWER {num+1}: I=12/4=4 A")
    raw = doc.tobytes()
    doc.close()
    return raw


def test_schema_and_rubric(rubric):
    assert rubric.questions[0].max_points == 10
    assert rubric.question("Q2").regions[0].page == 2
    assert len(rubric.digest()) == 16
    with pytest.raises(ValueError):
        Box(x0=0.8, y0=0.2, x1=0.1, y1=0.8)
    bad = rubric.model_dump()
    bad["questions"][0]["deductions"].append(bad["questions"][0]["deductions"][0])
    with pytest.raises(ValueError):
        Rubric.model_validate(bad)


def test_scoring_and_approval(rubric):
    q = rubric.question("Q1")
    finding = Finding(rule_id="ARITHMETIC", evidence="12/4=4", box=Box(x0=0.2,y0=0.2,x1=0.5,y1=0.4))
    result = QuestionResult(question_id="Q1", rubric_digest=rubric.digest(), findings=[finding])
    assert score_question(q, result) == 9
    assert approved_scores(rubric, {"Q1": result})[1] is None
    result.review_status = "approved"
    assert approved_scores(rubric, {"Q1": result})[0]["Q1"] == 9
    result.findings.append(finding)
    assert any("limit" in x for x in validate_findings(q, result))
    with pytest.raises(ValueError):
        score_question(q, result)
    result.findings = [finding]
    result.score_override = 8
    assert validate_findings(q, result)
    result.override_reason = "other acceptable approach with partial credit"
    assert score_question(q, result) == 8


def test_pdf_crop_and_approved_annotation(tmp_path, rubric, simple_pdf):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(simple_pdf)
    assert check_regions(pdf, rubric) == []
    image = render_region(pdf, rubric.question("Q1").regions[0], dpi=100)
    assert image.width > 100 and image.height > 100
    with fitz.open(pdf) as doc:
        bounds = image_location_to_pdf(doc[0],rubric.question("Q1").regions[0],
                         Box(x0=0.1,y0=0.2,x1=0.5,y1=0.4))
        assert bounds.x0 < bounds.x1
    finding = Finding(rule_id="ARITHMETIC", evidence="12 / 4 = 4 A",
        explanation="Incorrect arithmetic", region_index=0, box=Box(x0=0.1,y0=0.2,x1=0.5,y1=0.4))
    result = QuestionResult(question_id="Q1",rubric_digest=rubric.digest(),
                            findings=[finding],review_status="approved",reviewed_by="TA")
    graded = annotated_pdf(pdf, rubric, {"Q1":result})
    with fitz.open(stream=graded, filetype="pdf") as doc:
        assert len(doc) == 2
        annotation_types = [a.type[1] for a in doc[0].annots()]
        assert len(annotation_types) >= 3  # score note, error rectangle, error note
        assert annotation_types.count("Square") == 1
        assert len(list(doc[1].annots() or [])) == 0


def test_store_approval_and_placeholder(tmp_path, rubric, simple_pdf):
    store = Store(tmp_path)
    sid, imported = store.import_pdf("student a.pdf", simple_pdf)
    assert imported
    sid2, imported2 = store.import_pdf("same_upload.pdf", simple_pdf)
    assert sid2 == sid and imported2 is False
    q = rubric.question("Q1")
    result = analyze(store, sid, q, rubric)
    assert result.model_status == "not_connected"
    assert result.review_status == "needs_review"
    assert not result.findings
    assert len(store.audit_rows(sid)) == 1
    result.findings.append(Finding(rule_id="ARITHMETIC", evidence="I=12/4=4 A",origin="manual"))
    result.review_status = "approved"
    result.reviewed_by = "TA"
    store.save_result(sid, result, reviewer="TA", action="approve")
    assert store.get_result(sid, "Q1").review_status == "approved"
    with pytest.raises(ValueError):
        analyze(store, sid, q, rubric)
    store.delete_submission(sid)
    assert not store.submissions()
    assert len(store.audit_rows(sid)) == 0


def test_incomplete_never_gets_total(rubric):
    results = {"Q1": QuestionResult(question_id="Q1",rubric_digest=rubric.digest(),
               review_status="approved",reviewed_by="TA")}
    _, total = approved_scores(rubric, results)
    assert total is None
    results["Q2"] = QuestionResult(question_id="Q2",rubric_digest=rubric.digest(),
                                  review_status="approved",reviewed_by="TA")
    _, total = approved_scores(rubric, results)
    assert total == 20
