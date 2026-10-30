# Local Exam Grader

A **fully local**, instructor-supervised Python application for grading scanned handwritten exams and homework using a configurable deduction rubric. The LLM connection is intentionally left as a template for your own local vision-language model (VLM). No cloud APIs are included.

## Quick start

Python 3.10+ recommended. Works on Windows, macOS and Linux; native Windows requires no WSL.

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Alternatively, double-click `start_windows.bat` on Windows or run `./start_mac_linux.sh` on macOS/Linux to set up the environment and start the application.

Visit `http://localhost:8501`. The provided Streamlit configuration binds to **127.0.0.1 only** and turns off usage telemetry. Do not publicly expose the app or its database.

Optional **local Tesseract** OCR hint:
- Windows: install the Tesseract executable and add it to PATH, then restart your shell.
- macOS: `brew install tesseract`.
- Ubuntu: `sudo apt install tesseract-ocr`.

OCR is optional. Tesseract's handwritten math accuracy is often poor; it should never be relied on for automatic grading. The local VLM is intended to interpret handwriting, equations and diagrams after you connect it.

## Workflow

1. **Rubric & Regions:** load/edit the rubric JSON. Define max marks, reference solutions, deduction rules, and rectangular page areas for each answer. A question may use multiple regions across multiple pages.
2. **Import PDFs:** upload one or many scanned student papers. No student files leave the machine.
3. **Analyze:** crop answer regions, run optional local OCR, and (once connected) ask your local VLM to suggest deductions. If not connected, the system creates only transcription hints and flags every question for review. `Analyze` never approves a grade.
4. **Review:** verify/correct OCR, inspect scanned handwriting, accept/reject proposed deductions or add deductions manually, optionally override a grade with an explanation, then **approve** with reviewer initials.
5. **Export:** download only approved marks as CSV, and annotated versions of PDFs. Papers with unapproved questions have blank total grades.

To avoid grading a wrong region, **preview question crops on one of your actual PDF layouts before batch analysis**. The sample rubric assumes Q1 is on page 1 and Q2 on page 2; you must adjust it to your exam structure. If students have different PDF layouts, separate submissions into layout-consistent batches and use the relevant rubric each time.

## Connecting your own local VLM

Open `grader_core/local_llm_adapter.py`:

1. Implement `analyze_question(question, region_image_paths, ocr_hints)` using your **local multimodal model**. Paths refer to temporary PNG image files, not a URL. Models that only support text will not reliably read handwritten diagrams.
2. Ask your model to return a Python `dict` matching the `ModelAnalysis` schema in `grader_core/schema.py`. A documented example is provided in the adapter file.
3. Set `MODEL_CONNECTED = True` at the top of the adapter.
4. Restart Streamlit. Try a **synthetic exam** before using real student work.

A robust implementation should instruct the model to follow the supplied rubric, describe exactly what it sees, cite visual evidence, use only existing rule IDs, preserve alternative correct solutions and follow-through credit, and place any ambiguous symbols or explanations in `uncertainties`. `box` must give normalized coordinates within the specific answer-region image; leave null rather than guessing a location.

### Scoring rules

- Scoring is **deterministic Python code**, never an LLM-generated final grade.
- Each accepted deduction must match an ID in the rubric, have a reason or evidence, respect its max applications, and avoid exclusive-group conflicts.
- Scores are floored at zero; rubric version hashes invalidate stale approvals.
- The local model only *proposes* findings. The reviewer is responsible for approving each question; no-model mode cannot produce final grades by itself.
- A documented manual override is available for alternative correct solutions or other rubric exceptions.
- Original scanned pages are preserved. Red rectangles appear only when a reviewer/model supplied a normalized evidence box; otherwise a PDF comment is placed near the question area.
- Audit history is stored in local SQLite.

## Data and security

`private_data/` contains the original PDFs, a SQLite database with grades, the local rubric, and audit history. **Treat this directory as sensitive.** It is excluded via `.gitignore`. Delete this folder to reset the entire workspace (only after taking any needed backups). The **Delete submission** interface also removes its saved PDF, grade records and audit history.

No external APIs, model downloads, telemetry, or remote OCR calls are made by this code. Note that installing packages or choosing a model may separately involve Internet access; configure your own model to use a local runtime/endpoint only. The app is designed for one trusted instructor on one computer, not simultaneous multiuser editing or an open Internet server. Access-control, retention policy, student privacy compliance, and institutional approval remain your responsibility.

## Limitations and next upgrades

- Answer regions currently use **manual normalized page rectangles**, not automatic question detection. Verify layouts before batch processing.
- No local model is bundled; this is a deliberate adapter placeholder. Without one, the app is a working PDF review and manual-grading tool, **not** an automatic handwritten-exam grader.
- No robust handwritten-math/circuit-diagram recognition is claimed without the model adapter. Tesseract only provides a hint.
- Low model confidence or missing evidence triggers review; confidence thresholds are heuristic and are not calibrated probabilities.
- PDF notes display in viewers that support standard PDF annotations (Adobe Acrobat, browser PDF viewers with comment support, etc.).
- The app does not detect rubric omissions, plagiarism, or all alternative solutions.

## Project layout

```
app.py                         Streamlit UI
examples/rubric.json           Editable example rubric
examples/synthetic_exam.pdf    Sample scanned-style PDF (not actual student work)
grader_core/schema.py          Pydantic data contracts
grader_core/storage.py         SQLite / original PDF persistence
grader_core/pdf_tools.py       Answer crops and annotated PDF output
grader_core/ocr.py             Optional offline OCR
grader_core/local_llm_adapter.py  FILL THIS IN to connect local VLM
grader_core/grading.py        Scoring validation and calculation
grader_core/pipeline.py       OCR / model orchestration
tests/test_core.py             Unit and PDF annotation tests
start_windows.bat               Windows launch script
start_mac_linux.sh              macOS/Linux launch script
```

## Tests

```bash
python -m pytest -q
```

Tests cover rubric validation, deduction scoring, multiple regions, PDF annotations, CSV/approval behavior, and a deliberately unconnected model workflow.
