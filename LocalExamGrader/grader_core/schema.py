"""Strict schemas for the rubric, visual evidence, and grading decisions."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Box(StrictModel):
    """Normalized x0,y0,x1,y1 coordinates, relative to the rendered region."""
    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.x0 >= self.x1 or self.y0 >= self.y1:
            raise ValueError("Box must have positive width and height")
        return self


class Region(StrictModel):
    page: int = Field(ge=1, description="1-based page number")
    box: Box = Field(default_factory=lambda: Box(x0=0.04, y0=0.07, x1=0.96, y1=0.94))


class DeductionRule(StrictModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    points: float = Field(gt=0)
    detail: str = ""
    max_applications: int = Field(default=1, ge=1, le=100)
    exclusive_group: str | None = None


class Question(StrictModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    max_points: float = Field(gt=0)
    model_solution: str = ""
    regions: list[Region] = Field(min_length=1)
    deductions: list[DeductionRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_rules(self):
        ids = [d.id for d in self.deductions]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate deduction IDs in {self.id}")
        return self


class Rubric(StrictModel):
    title: str = Field(min_length=1)
    version: str = Field(min_length=1)
    questions: list[Question] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_questions(self):
        ids = [q.id for q in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate question IDs")
        return self

    def question(self, question_id: str) -> Question:
        for question in self.questions:
            if question.id == question_id:
                return question
        raise KeyError(question_id)

    def digest(self) -> str:
        raw = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class Finding(StrictModel):
    rule_id: str
    explanation: str = ""
    evidence: str = ""
    region_index: int = Field(default=0, ge=0)
    box: Box | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    accepted: bool = True
    origin: Literal["model", "manual"] = "model"


class ModelAnalysis(StrictModel):
    transcription: str = ""
    uncertainties: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)


class QuestionResult(StrictModel):
    question_id: str
    rubric_digest: str
    transcription: str = ""
    ocr_hint: str = ""
    model_status: Literal["not_connected", "completed", "error", "not_run"] = "not_run"
    review_reasons: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    review_status: Literal["needs_review", "ready_for_approval", "approved"] = "needs_review"
    reviewed_by: str = ""
    reviewed_at: str = ""
    review_notes: str = ""
    score_override: float | None = None
    override_reason: str = ""
