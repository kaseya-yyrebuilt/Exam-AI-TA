"""Pure, deterministic score engine and safety validation."""
from __future__ import annotations

from collections import Counter, defaultdict
from .schema import Question, QuestionResult, Rubric


def validate_findings(question: Question, result: QuestionResult) -> list[str]:
    errors = []
    rules = {rule.id: rule for rule in question.deductions}
    counts: Counter[str] = Counter()
    groups = defaultdict(set)
    for i, item in enumerate(result.findings, 1):
        if not item.accepted:
            continue
        rule = rules.get(item.rule_id)
        if rule is None:
            errors.append(f"Finding {i}: unknown deduction '{item.rule_id}'")
            continue
        if item.region_index >= len(question.regions):
            errors.append(f"Finding {i}: invalid answer region")
        if not item.explanation.strip() and not item.evidence.strip():
            errors.append(f"Finding {i}: add a rationale or evidence")
        counts[item.rule_id] += 1
        if rule.exclusive_group:
            groups[rule.exclusive_group].add(rule.id)
    for rule_id, count in counts.items():
        if rule_id in rules and count > rules[rule_id].max_applications:
            errors.append(f"Deduction {rule_id} used {count} times, limit {rules[rule_id].max_applications}")
    for group, ids in groups.items():
        if len(ids) > 1:
            errors.append(f"Mutually exclusive deductions used together in group '{group}': {', '.join(sorted(ids))}")
    if result.score_override is not None:
        if not (0 <= result.score_override <= question.max_points):
            errors.append("Manual score must be within the question's point range")
        if not result.override_reason.strip():
            errors.append("Explain why the score was manually overridden")
    return errors


def score_question(question: Question, result: QuestionResult) -> float:
    errors = validate_findings(question, result)
    if errors:
        raise ValueError("; ".join(errors))
    if result.score_override is not None:
        return round(result.score_override, 2)
    lookup = {rule.id: rule.points for rule in question.deductions}
    deduction = sum(lookup[f.rule_id] for f in result.findings if f.accepted)
    return round(max(0.0, question.max_points - deduction), 2)


def approved_scores(rubric: Rubric, saved: dict[str, QuestionResult]):
    """Return only approved scores for the current rubric; incomplete totals stay blank."""
    by_question = {}
    for q in rubric.questions:
        result = saved.get(q.id)
        if result is None or result.review_status != "approved" or result.rubric_digest != rubric.digest():
            by_question[q.id] = None
            continue
        try:
            by_question[q.id] = score_question(q, result)
        except ValueError:
            by_question[q.id] = None
    complete = all(v is not None for v in by_question.values())
    total = round(sum(v for v in by_question.values() if v is not None), 2) if complete else None
    return by_question, total
