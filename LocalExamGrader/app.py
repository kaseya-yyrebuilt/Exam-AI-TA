"""Local Exam Grader — private, rubric-based PDF grading with human approval.

Run: streamlit run app.py
This application makes no HTTP requests or third-party model calls.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import os
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from grader_core import local_llm_adapter
from grader_core.grading import approved_scores, score_question, validate_findings
from grader_core.ocr import available as tesseract_available
from grader_core.pdf_tools import annotated_pdf, check_regions, render_page, render_region
from grader_core.pipeline import analyze, blank_result
from grader_core.schema import Box, Finding, QuestionResult, Rubric
from grader_core.storage import Store, now

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("EXAM_GRADER_DATA", str(ROOT / "private_data"))).expanduser()
store = Store(DATA)
st.set_page_config(page_title="Local Exam Grader", page_icon="📝", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 1.55rem; max-width: 1500px;}
[data-testid="stSidebar"] {background: #eef3f9;}
h1 {letter-spacing: -0.025em;}
.small-muted {color: #66768a; font-size: 0.88rem;}
</style>
""", unsafe_allow_html=True)


def load_rubric() -> Rubric:
    contents = store.read_rubric()
    if contents is None:
        contents = (ROOT / "examples" / "rubric.json").read_text(encoding="utf-8")
        store.save_rubric(contents)
    return Rubric.model_validate_json(contents)


try:
    rubric = load_rubric()
except Exception as error:
    st.error(f"Stored rubric is invalid: {error}. Repair the file under private_data/rubric.json.")
    st.stop()


submissions = store.submissions()
by_id = {row["id"]: row for row in submissions}


def get_sid(key: str) -> str | None:
    if not submissions:
        st.info("Start by importing at least one scanned PDF in **Import PDFs**.")
        return None
    return st.selectbox("Student submission", list(by_id),
                        format_func=lambda sid: f"{by_id[sid]['student_label']} · {sid[:7]}", key=key)


def get_question(key: str):
    qid = st.selectbox("Question", [q.id for q in rubric.questions],
                       format_func=lambda qid: f"{qid} — {rubric.question(qid).title}", key=key)
    return rubric.question(qid)


def progress_for(sid: str):
    saved = store.results(sid)
    grades, total = approved_scores(rubric, saved)
    count = sum(score is not None for score in grades.values())
    return saved, grades, total, count


def show_region_previews(pdf_path: str, question, dpi=125):
    for idx, region in enumerate(question.regions):
        with st.expander(f"Answer region {idx + 1} · page {region.page}", expanded=(idx == 0)):
            try:
                st.image(render_region(pdf_path, region, dpi=dpi),
                         caption=f"Region {idx+1}: page {region.page} · normalized bounding box {region.box.model_dump()}",
                         use_container_width=True)
            except Exception as error:
                st.error(f"Could not render answer region: {error}")


def make_export_rows() -> list[dict]:
    rows = []
    for sub in store.submissions():
        saved, grades, total, approved_count = progress_for(sub["id"])
        row = {"student": sub["student_label"], "filename": sub["filename"],
               "approved_questions": approved_count, "question_count": len(rubric.questions),
               "status": "COMPLETE" if total is not None else "INCOMPLETE"}
        for q in rubric.questions:
            row[q.id] = "" if grades[q.id] is None else grades[q.id]
        row["total"] = "" if total is None else total
        row["max_total"] = sum(q.max_points for q in rubric.questions)
        rows.append(row)
    return rows


def export_csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    # Prevent untrusted names / filenames from becoming formulas in spreadsheet apps.
    for row in rows:
        safe = {k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v)
                for k, v in row.items()}
        writer.writerow(safe)
    return buffer.getvalue().encode("utf-8-sig")


def parse_box(raw: str) -> Box | None:
    if not raw.strip():
        return None
    values = [float(x.strip()) for x in raw.split(",")]
    if len(values) != 4:
        raise ValueError("Bounding box needs 4 comma-separated fractions: x0,y0,x1,y1")
    return Box(x0=values[0], y0=values[1], x1=values[2], y1=values[3])


st.sidebar.markdown("## 📝 Local Exam Grader")
st.sidebar.caption("Private grading workspace · No cloud APIs")
page = st.sidebar.radio("Navigation", ["Dashboard", "Rubric & Regions", "Import PDFs", "Analyze", "Review", "Export"],
                        label_visibility="collapsed")
st.sidebar.divider()
st.sidebar.write(f"**Rubric:** {rubric.title}")
st.sidebar.caption(f"Version {rubric.version} · {len(rubric.questions)} questions")
st.sidebar.caption("Local VLM: " + ("connected" if local_llm_adapter.MODEL_CONNECTED else "not connected (manual review)"))
st.sidebar.caption("Tesseract OCR: " + ("available" if tesseract_available() else "not installed"))
st.sidebar.caption("Grades are never released automatically.")

if page == "Dashboard":
    st.title("Exam grading overview")
    st.caption("Import PDFs, map answers to rubric questions, examine proposed deductions, and approve each question.")
    complete = 0
    approved_count = 0
    pending_count = 0
    dashboard_rows = []
    for sub in submissions:
        saved, grades, total, n_approved = progress_for(sub["id"])
        approved_count += n_approved
        pending_count += len(rubric.questions) - n_approved
        complete += int(total is not None)
        dashboard_rows.append({"Student": sub["student_label"], "Pages": sub["pages"],
                               "Approved": f"{n_approved}/{len(rubric.questions)}",
                               "Final score": f"{total:g}" if total is not None else "—",
                               "State": "Complete" if total is not None else "Needs review"})
    a, b, c, d = st.columns(4)
    a.metric("Submissions", len(submissions))
    b.metric("Approved papers", complete)
    c.metric("Approved question grades", approved_count)
    d.metric("Questions awaiting approval", pending_count)
    st.subheader("Submissions")
    if dashboard_rows:
        st.dataframe(pd.DataFrame(dashboard_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No PDFs imported yet. Go to **Import PDFs** to begin.")
    st.markdown("""**Recommended workflow:** (1) verify the rubric and answer regions, (2) import scanned exams,
    (3) run OCR/model analysis, (4) review handwriting and proposed deductions, (5) approve each question,
    then (6) export annotated PDFs and a gradebook.""")
    st.warning("Prototype for instructor-supervised grading. OCR can misread handwritten calculations; do not treat model proposals as final grades.")

elif page == "Rubric & Regions":
    st.title("Rubric & answer regions")
    st.caption("Edit the local JSON rubric, including deduction IDs, maximum marks, solutions, and normalized answer areas.")
    if "rubric_editor" not in st.session_state:
        st.session_state.rubric_editor = json.dumps(rubric.model_dump(mode="json"), indent=2)
    with st.expander("Instructions for region boxes and deduction rules", expanded=False):
        st.markdown("""Each answer region has a **1-based** page number and an `(x0,y0,x1,y1)` box where values run
        from 0 to 1. For example `(0.05,0.2,0.95,0.85)` selects the central portion of that page.
        Use multiple regions if an answer spans pages. Regions are shared across the uploaded papers—use
        a separate rubric for different exam layouts. A criterion's `max_applications` prevents repeated
        penalties and `exclusive_group` prevents incompatible criteria from being charged together.
        Include full marking conditions and follow-through rules in `detail` for the local model and reviewer.""")
    uploaded_rubric = st.file_uploader("Load a rubric JSON", type="json", key="upload_rubric")
    if uploaded_rubric and st.button("Copy uploaded JSON into editor"):
        st.session_state.rubric_editor = uploaded_rubric.getvalue().decode("utf-8")
        st.rerun()
    st.text_area("Rubric JSON", key="rubric_editor", height=440)
    if st.button("Validate and save rubric", type="primary"):
        try:
            new_rubric = Rubric.model_validate_json(st.session_state.rubric_editor)
            store.save_rubric(json.dumps(new_rubric.model_dump(mode="json"), indent=2))
            st.success("Rubric saved. Previously approved grades using an older rubric will no longer count as current.")
            st.rerun()
        except Exception as error:
            st.error(f"Rubric validation failed: {error}")
    st.divider()
    st.subheader("Question layout preview")
    if submissions:
        sid = get_sid("rubric_sid")
        q = get_question("rubric_question")
        if sid:
            problems = check_regions(by_id[sid]["pdf_path"], rubric)
            if problems:
                st.error("Region layout problems: " + "; ".join(problems))
            show_region_previews(by_id[sid]["pdf_path"], q)
            st.caption("If a region misses the handwriting, edit the page number or normalized coordinates above.")
    else:
        st.info("Import an example PDF to preview and adjust answer regions.")

elif page == "Import PDFs":
    st.title("Import scanned student work")
    st.caption("Uploaded scans are saved only inside the local private_data folder.")
    if st.session_state.get("import_success"):
        st.success(st.session_state.pop("import_success"))
    files = st.file_uploader("Choose scanned PDF exams or homework", type="pdf", accept_multiple_files=True)
    if st.button("Import selected PDFs", type="primary", disabled=not files):
        count = 0
        for file in files:
            try:
                sid, added = store.import_pdf(file.name, file.getvalue())
                count += int(added)
                if not added:
                    st.info(f"Duplicate skipped: {file.name}")
            except Exception as error:
                st.error(f"{file.name}: {error}")
        if count:
            st.session_state["import_success"] = f"Imported {count} PDF(s). Verify question regions before analysis."
            st.rerun()
    if submissions:
        st.subheader("Stored submissions")
        st.dataframe(pd.DataFrame([{"Student label": s["student_label"], "File": s["filename"],
                                   "Pages": s["pages"], "Imported": s["uploaded_at"]} for s in submissions]),
                     use_container_width=True, hide_index=True)
        sid = get_sid("import_sid")
        if sid:
            with st.expander("Rename or delete a submission"):
                label = st.text_input("Student label", value=by_id[sid]["student_label"], key=f"rename_{sid}")
                if st.button("Save student label"):
                    try:
                        store.rename(sid, label)
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))
                st.caption("Deleting a submission also deletes its original PDF, grade decisions and audit history.")
                confirm = st.checkbox("I understand this permanently deletes this submission", key=f"confirm_{sid}")
                if st.button("Delete submission", disabled=not confirm):
                    store.delete_submission(sid)
                    st.rerun()

elif page == "Analyze":
    st.title("Extract handwriting & propose deductions")
    st.caption("Per-question image crops are processed locally. Tesseract supplies an optional hint; your VLM adapter is responsible for meaningful handwriting and diagram understanding.")
    sid = get_sid("analyze_sid")
    if sid:
        sub = by_id[sid]
        q = get_question("analyze_q")
        errors = check_regions(sub["pdf_path"], rubric)
        if errors:
            st.error("Fix page mappings first: " + "; ".join(errors))
        else:
            show_region_previews(sub["pdf_path"], q, dpi=100)
            old = store.get_result(sid, q.id)
            if old:
                st.info(f"{q.id}: {old.review_status.replace('_', ' ')} · local model: {old.model_status}")
            replace_approved = st.checkbox("Allow reanalysis of already-approved grades (overwrites human review)", value=False)
            left, middle, right = st.columns(3)
            with left:
                selected_run = st.button("Analyze this question", disabled=not(errors == []))
            with middle:
                paper_run = st.button("Analyze entire paper", disabled=not(errors == []))
            with right:
                all_run = st.button("Analyze every imported paper", disabled=not(errors == []))
            if selected_run or paper_run or all_run:
                selected_students = [sid] if not all_run else [s["id"] for s in submissions]
                selected_questions = [q] if selected_run else rubric.questions
                completed, skipped = 0, 0
                progress = st.progress(0)
                total = len(selected_students) * len(selected_questions)
                with st.spinner("Running local OCR and available model adapter..."):
                    for student_id in selected_students:
                        for question in selected_questions:
                            try:
                                problems = check_regions(store.submission(student_id)["pdf_path"],
                                                         Rubric(title=rubric.title, version=rubric.version, questions=[question]))
                                if problems:
                                    st.warning(f"Skipped {store.submission(student_id)['filename']} / {question.id}: {problems}")
                                    skipped += 1
                                else:
                                    analyze(store, student_id, question, rubric, force=replace_approved)
                                    completed += 1
                            except Exception as error:
                                skipped += 1
                                st.warning(f"Skipped {store.submission(student_id)['filename']} / {question.id}: {error}")
                            progress.progress((completed + skipped) / total)
                st.success(f"Analyzed {completed} question(s). Skipped {skipped}. Check **Review** before grading.")
            st.divider()
            result = store.get_result(sid, q.id)
            if result:
                st.subheader("Current transcription and model results")
                st.code(result.transcription or "No transcription available", language="text")
                if result.review_reasons:
                    for reason in result.review_reasons:
                        st.warning(reason)
                if not local_llm_adapter.MODEL_CONNECTED:
                    st.info("Local model not connected: analysis is OCR-only and all grading remains manual. Edit grader_core/local_llm_adapter.py to enable VLM inference.")

elif page == "Review":
    st.title("Review & approve grading")
    st.caption("Instructor remains responsible for checking handwriting, deductions and scores. Nothing is automatically approved.")
    sid = get_sid("review_sid")
    if sid:
        q = get_question("review_q")
        sub = by_id[sid]
        st.markdown(f"**{sub['student_label']}** · {q.id} · maximum **{q.max_points:g} points**")
        issues = check_regions(sub["pdf_path"], Rubric(title=rubric.title,version=rubric.version,questions=[q]))
        if issues:
            st.error("Incorrect answer regions: " + "; ".join(issues))
            st.stop()
        result = store.get_result(sid, q.id) or blank_result(q, rubric)
        if result.rubric_digest != rubric.digest():
            st.error("This grading record belongs to an OLD rubric. Re-analyze before approving or editing.")
            st.stop()
        left, right = st.columns([1.3, 1], gap="large")
        with left:
            st.subheader("Scanned work")
            show_region_previews(sub["pdf_path"], q, dpi=160)
        with right:
            st.subheader("Review findings")
            st.caption(f"Model: {result.model_status} · Status: {result.review_status.replace('_',' ')}")
            for reason in result.review_reasons:
                st.warning(reason)
            rules = {r.id: r for r in q.deductions}
            if result.findings:
                for f in result.findings:
                    rule = rules.get(f.rule_id)
                    st.caption(f"{'✓' if f.accepted else '×'} {f.rule_id} · "
                               f"{rule.points if rule else '?'} pt(s) · {f.explanation[:120]}")
            else:
                st.info("No deductions have been proposed. This does NOT mean full marks: review the student's work.")
            try:
                tentative = score_question(q, result)
                if result.model_status == "completed" or result.review_status == "approved" or result.findings or result.score_override is not None:
                    st.metric("Current proposed score", f"{tentative:g} / {q.max_points:g}")
                else:
                    st.metric("Grade", "Not determined")
            except ValueError as error:
                st.error(f"Grading validation: {error}")
        st.divider()
        with st.form(f"review_edit_{sid}_{q.id}"):
            transcription = st.text_area("Verified transcription / notes on student's working", value=result.transcription,
                                         height=130, help="Correct handwriting interpretation before deciding deductions.")
            st.markdown("#### Existing deductions")
            edited = []
            for i, finding in enumerate(result.findings):
                rule = rules.get(finding.rule_id)
                with st.expander(f"{i+1}. {finding.rule_id} · {rule.label if rule else 'Unknown'}", expanded=False):
                    accepted = st.checkbox("Apply this deduction", value=finding.accepted, key=f"accept_{sid}_{q.id}_{i}")
                    explanation = st.text_area("Why?", value=finding.explanation, key=f"why_{sid}_{q.id}_{i}")
                    evidence = st.text_input("Exact work supporting deduction", value=finding.evidence,
                                             key=f"evidence_{sid}_{q.id}_{i}")
                    reg_index = st.number_input("Region index (0-based)", min_value=0, max_value=len(q.regions)-1,
                                                value=min(finding.region_index, len(q.regions)-1), step=1,
                                                key=f"reg_{sid}_{q.id}_{i}")
                    oldbox = ", ".join(str(v) for v in (finding.box.x0, finding.box.y0,
                              finding.box.x1, finding.box.y1)) if finding.box else ""
                    box_raw = st.text_input("Evidence box x0,y0,x1,y1 (optional, normalized to answer region)",
                                            value=oldbox, key=f"box_{sid}_{q.id}_{i}")
                    edited.append((finding, accepted, explanation, evidence, int(reg_index), box_raw))
            st.markdown("#### Instructor decision")
            override_flag = st.checkbox("Override calculated score", value=result.score_override is not None,
                                        help="Use when the rubric's listed deductions cannot represent the fair score.")
            override = st.number_input("Manual score", min_value=0.0, max_value=float(q.max_points), step=0.5,
                                       value=float(result.score_override if result.score_override is not None else q.max_points),
                                       disabled=not override_flag)
            override_reason = st.text_input("Reason for overriding", value=result.override_reason,
                                            disabled=not override_flag)
            reviewer = st.text_input("Reviewer name / initials", value=result.reviewed_by,
                                     help="Required to approve grades.")
            review_notes = st.text_area("Instructor feedback / note to include in PDF", value=result.review_notes, height=80)
            keep_rejected = st.checkbox("Keep rejected proposed deductions in the audit view", value=True)
            c1, c2 = st.columns(2)
            save_draft = c1.form_submit_button("Save review draft", use_container_width=True)
            approve = c2.form_submit_button("Approve question grade", type="primary", use_container_width=True)
        if save_draft or approve:
            try:
                updated = result.model_copy(deep=True)
                updated.transcription = transcription
                updated.review_notes = review_notes
                updated.reviewed_by = reviewer.strip()
                updated.override_reason = override_reason.strip() if override_flag else ""
                updated.score_override = float(override) if override_flag else None
                updated.findings = []
                for original, accepted, explanation, evidence, reg_index, box_raw in edited:
                    updated_finding = original.model_copy(deep=True)
                    updated_finding.accepted = accepted
                    updated_finding.explanation = explanation.strip()
                    updated_finding.evidence = evidence.strip()
                    updated_finding.region_index = reg_index
                    updated_finding.box = parse_box(box_raw)
                    if accepted or keep_rejected:
                        updated.findings.append(updated_finding)
                if approve:
                    if not reviewer.strip():
                        raise ValueError("A reviewer name or initials is required to approve a grade")
                    errors = validate_findings(q, updated)
                    if errors:
                        raise ValueError("; ".join(errors))
                    score_question(q, updated)
                    updated.review_status = "approved"
                    updated.reviewed_at = now()
                else:
                    updated.review_status = "needs_review"
                    updated.reviewed_at = ""
                store.save_result(sid, updated, reviewer=reviewer.strip(),
                                  action="approve" if approve else "save_draft")
                st.success("Grade approved and saved." if approve else "Draft saved. Grade still requires approval.")
                st.rerun()
            except Exception as error:
                st.error(f"Could not save: {error}")
        st.divider()
        st.markdown("#### Add a deduction manually")
        if q.deductions:
            with st.form(f"manual_{sid}_{q.id}"):
                chosen = st.selectbox("Rubric rule", [rule.id for rule in q.deductions],
                                      format_func=lambda rid: f"{rid} — {rules[rid].label} (−{rules[rid].points:g})")
                manual_why = st.text_input("Reason", placeholder="E.g., 12 / 4 evaluated as 4")
                manual_evidence = st.text_input("Evidence in student's handwriting", placeholder="I = 12/4 = 4 A")
                manual_region = st.selectbox("Answer region", list(range(len(q.regions))),
                                             format_func=lambda i: f"Region {i+1}, page {q.regions[i].page}")
                manual_box = st.text_input("Optional evidence box x0,y0,x1,y1", placeholder="0.20,0.32,0.78,0.40")
                add = st.form_submit_button("Add deduction")
            if add:
                try:
                    if not manual_why.strip() and not manual_evidence.strip():
                        raise ValueError("Supply a reason or evidence")
                    finding = Finding(rule_id=chosen, explanation=manual_why.strip(), evidence=manual_evidence.strip(),
                                      region_index=manual_region, box=parse_box(manual_box), origin="manual")
                    updated = result.model_copy(deep=True)
                    updated.findings.append(finding)
                    updated.review_status = "needs_review"
                    updated.reviewed_at = ""
                    errors = validate_findings(q, updated)
                    if errors:
                        raise ValueError("; ".join(errors))
                    store.save_result(sid, updated, reviewer=updated.reviewed_by, action="add_deduction")
                    st.rerun()
                except Exception as error:
                    st.error(f"Could not add deduction: {error}")
        with st.expander("Review audit history"):
            history = store.audit_rows(sid)
            st.dataframe(pd.DataFrame(history), hide_index=True, use_container_width=True) if history else st.caption("No changes yet")

elif page == "Export":
    st.title("Export approved grades and annotated PDFs")
    st.caption("Only instructor-approved, current-rubric question grades are included. Incomplete papers have blank final totals.")
    rows = make_export_rows()
    if not rows:
        st.info("Import and review papers before exporting.")
    else:
        df = pd.DataFrame(rows)
        st.dataframe(df, hide_index=True, use_container_width=True)
        st.download_button("Download approved gradebook (CSV)", data=export_csv_bytes(rows),
                           file_name="approved_gradebook.csv", mime="text/csv", type="primary")
        st.divider()
        sid = get_sid("export_sid")
        if sid:
            sub = by_id[sid]
            saved, grades, total, approved_count = progress_for(sid)
            if approved_count:
                st.download_button("Download annotated PDF for selected student",
                                   data=annotated_pdf(sub["pdf_path"], rubric, saved),
                                   file_name=f"{Path(sub['filename']).stem}_graded.pdf", mime="application/pdf")
            else:
                st.warning("No approved question grades for this student yet; no annotated PDF is available.")
        include_pdf = st.checkbox("Include annotated PDFs for all submissions with approved questions", value=True)
        if st.button("Prepare local export ZIP"):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("approved_gradebook.csv", export_csv_bytes(rows))
                archive.writestr("README.txt", "Only approved grades exported. Blank total = incomplete paper.\n"
                                 "PDF comments are instructor-approved annotations.\n")
                if include_pdf:
                    for sub in store.submissions():
                        saved, _, _, approved_count = progress_for(sub["id"])
                        if approved_count:
                            name = f"annotated/{sub['id'][:8]}_{Path(sub['filename']).stem[:80]}.pdf"
                            archive.writestr(name, annotated_pdf(sub["pdf_path"], rubric, saved))
            st.session_state["local_grader_export_zip"] = buffer.getvalue()
        if st.session_state.get("local_grader_export_zip"):
            st.download_button("Download complete export ZIP", data=st.session_state["local_grader_export_zip"],
                               file_name="approved_exam_grading.zip", mime="application/zip")
