"""Offline PDF cropping, rendering and native PDF annotations."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image, ImageOps

from .schema import Box, Region, Rubric, QuestionResult


def page_region_rect(page, region: Region) -> fitz.Rect:
    p = page.rect
    b = region.box
    return fitz.Rect(p.x0 + b.x0 * p.width, p.y0 + b.y0 * p.height,
                     p.x0 + b.x1 * p.width, p.y0 + b.y1 * p.height)


def check_regions(pdf_path: str | Path, rubric: Rubric) -> list[str]:
    errors = []
    with fitz.open(pdf_path) as document:
        if document.is_encrypted:
            return ["Encrypted PDF is not supported"]
        for question in rubric.questions:
            for region in question.regions:
                if region.page > len(document):
                    errors.append(f"{question.id}: page {region.page} does not exist ({len(document)} pages)")
    return errors


def render_page(pdf_path: str | Path, page_number: int, dpi: int = 100) -> Image.Image:
    with fitz.open(pdf_path) as document:
        page = document[page_number - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def render_region(pdf_path: str | Path, region: Region, dpi: int = 170, enhance: bool = False) -> Image.Image:
    with fitz.open(pdf_path) as document:
        if region.page > len(document):
            raise ValueError(f"Page {region.page} not present in PDF")
        page = document[region.page - 1]
        clip = page_region_rect(page, region)
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=clip, alpha=False)
        result = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if enhance:
        result = ImageOps.autocontrast(result, cutoff=1)
    return result


def image_location_to_pdf(page, region: Region, location: Box) -> fitz.Rect:
    parent = page_region_rect(page, region)
    return fitz.Rect(parent.x0 + location.x0 * parent.width, parent.y0 + location.y0 * parent.height,
                     parent.x0 + location.x1 * parent.width, parent.y0 + location.y1 * parent.height)


def annotated_pdf(pdf_path: str | Path, rubric: Rubric, saved: dict[str, QuestionResult]) -> bytes:
    """Annotate only approved current-rubric decisions, leaving original scans intact."""
    document = fitz.open(pdf_path)
    try:
        for question in rubric.questions:
            result = saved.get(question.id)
            if not result or result.review_status != "approved" or result.rubric_digest != rubric.digest():
                continue
            from .grading import score_question
            try:
                score = score_question(question, result)
            except ValueError:
                continue
            first_region = question.regions[0]
            page = document[first_region.page - 1]
            anchor = page_region_rect(page, first_region)
            note = page.add_text_annot((max(2, min(anchor.x0, page.rect.width - 22)),
                                       max(2, min(anchor.y0, page.rect.height - 22))),
                                      f"{question.id}: {score:g}/{question.max_points:g} points\n"
                                      f"Reviewer: {result.reviewed_by}\n{result.review_notes}".strip())
            note.set_info(title="Local Exam Grader - approved score")
            note.update()
            for finding in result.findings:
                if not finding.accepted:
                    continue
                rule = next((r for r in question.deductions if r.id == finding.rule_id), None)
                if rule is None or finding.region_index >= len(question.regions):
                    continue
                region = question.regions[finding.region_index]
                page = document[region.page - 1]
                reference = page_region_rect(page, region)
                if finding.box is not None:
                    r = image_location_to_pdf(page, region, finding.box)
                    annotation = page.add_rect_annot(r)
                    annotation.set_colors(stroke=(0.88, 0.18, 0.20))
                    annotation.set_border(width=1.6)
                    annotation.set_info(title=f"{question.id}: -{rule.points:g} {rule.label}",
                                        content=f"{finding.explanation}\nEvidence: {finding.evidence}")
                    annotation.update()
                    point = (max(2, min(r.x1 + 2, page.rect.width - 22)),
                             max(2, min(r.y0, page.rect.height - 22)))
                else:
                    point = (max(2, min(reference.x0 + 24, page.rect.width - 22)),
                             max(2, min(reference.y0 + 24, page.rect.height - 22)))
                note = page.add_text_annot(point,
                                          f"{question.id} | -{rule.points:g}: {rule.label}\n"
                                          f"{finding.explanation}\nEvidence: {finding.evidence}")
                note.set_info(title="Local Exam Grader - instructor-approved deduction")
                note.update()
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()
