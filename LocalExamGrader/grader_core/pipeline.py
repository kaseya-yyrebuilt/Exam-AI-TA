"""OCR + optional local VLM inference. Nothing in this module uses the network."""
from __future__ import annotations

import tempfile
from pathlib import Path

from . import local_llm_adapter
from .ocr import ocr_image
from .pdf_tools import render_region
from .schema import ModelAnalysis, Question, QuestionResult, Rubric
from .storage import Store


REVIEW_CONFIDENCE = 0.80  # only a heuristic; never automatically approve


def blank_result(question: Question, rubric: Rubric) -> QuestionResult:
    return QuestionResult(question_id=question.id, rubric_digest=rubric.digest(),
                          review_reasons=["Not analyzed or graded; instructor review required"])


def analyze(store: Store, sid: str, question: Question, rubric: Rubric,
            force: bool = False) -> QuestionResult:
    existing = store.get_result(sid, question.id)
    if existing and existing.review_status == "approved" and not force:
        raise ValueError("This grade is approved. Reanalysis would overwrite it; use explicit force=True")

    pdf_path = store.submission(sid)["pdf_path"]
    hints: list[str] = []
    result = QuestionResult(question_id=question.id, rubric_digest=rubric.digest())
    with tempfile.TemporaryDirectory(prefix="local_exam_grader_") as temporary:
        paths: list[Path] = []
        for index, region in enumerate(question.regions):
            image = render_region(pdf_path, region, dpi=170, enhance=True)
            hint = ocr_image(image)
            hints.append(f"[Region {index+1}, page {region.page}]\n{hint}")
            path = Path(temporary) / f"region_{index}.png"
            image.save(path)
            paths.append(path)
        result.ocr_hint = "\n\n".join(hints)
        result.transcription = result.ocr_hint

        if not local_llm_adapter.MODEL_CONNECTED:
            result.model_status = "not_connected"
            result.review_reasons = ["No local VLM connected: OCR is only a hint, not a graded result"]
        else:
            try:
                raw = local_llm_adapter.analyze_question(question, paths, hints)
                output = ModelAnalysis.model_validate(raw)
                result.transcription = output.transcription
                result.findings = output.findings
                result.model_status = "completed"
                result.review_reasons = list(output.uncertainties)
                known_ids = {rule.id for rule in question.deductions}
                for i, finding in enumerate(result.findings, 1):
                    if finding.rule_id not in known_ids:
                        result.review_reasons.append(f"Finding {i} references unknown rubric ID: {finding.rule_id}")
                        finding.accepted = False
                    if finding.region_index >= len(question.regions):
                        result.review_reasons.append(f"Finding {i} has invalid image-region index")
                        finding.accepted = False
                    if not finding.evidence.strip():
                        result.review_reasons.append(f"Finding {i} lacks supporting evidence")
                    if finding.box is None:
                        result.review_reasons.append(f"Finding {i} has no localized evidence rectangle")
                    if finding.confidence is None or finding.confidence < REVIEW_CONFIDENCE:
                        result.review_reasons.append(f"Finding {i} needs confidence review")
                from .grading import validate_findings
                result.review_reasons += validate_findings(question, result)
                if not result.transcription.strip():
                    result.review_reasons.append("No transcription supplied")
                result.review_status = "needs_review" if result.review_reasons else "ready_for_approval"
            except Exception as error:
                result.model_status = "error"
                result.review_reasons = [f"Local model failed: {type(error).__name__}: {error}"]
    # Prior manual decisions are not silently carried forward after reanalysis.
    store.save_result(sid, result, reviewer="system", action="analyze")
    return result
