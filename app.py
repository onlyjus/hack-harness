"""FastAPI backend for DOE/NETL directive comparison.

Provides:
- POST /api/upload  — accept DOE + NETL PDF uploads, start comparison job
- GET  /api/status/{job_id} — SSE stream of progress updates
- GET  /api/report/{job_id} — final comparison report JSON
- GET  / — serves the frontend
"""

import asyncio
import html as html_mod
import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from pdf_extractor import extract_text, extract_first_n_pages
from directive_extractor import (
    extract_directive_info,
    extract_directive_sections,
    compare_directives,
    save_comparison_report,
)

load_dotenv(override=False)

app = FastAPI(title="DOE/NETL Directive Comparator")

# In-memory job store
jobs: dict[str, dict] = {}

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _validate_job_id(job_id: str):
    """Validate job_id is a safe hex string to prevent path traversal."""
    if not re.fullmatch(r'[a-f0-9]+', job_id):
        raise HTTPException(400, "Invalid job ID")


def _compute_alignment_pct(sbs: list) -> int:
    """Compute alignment percentage from section-by-section data."""
    total = len(sbs) if sbs else 1
    aligned = sum(1 for s in sbs if s.get("status") == "aligned")
    return round((aligned / total) * 100)


def _load_reports_from_disk():
    """Scan data/report_*.json on startup to populate the in-memory jobs index."""
    import glob
    for path in sorted(glob.glob("data/report_*.json")):
        try:
            job_id = Path(path).stem.replace("report_", "")
            if job_id in jobs:
                continue
            with open(path) as f:
                data = json.load(f)
            report = data[0] if isinstance(data, list) else data

            # Load sections if available
            sections_data = None
            spath = Path(f"data/sections_{job_id}.json")
            if spath.exists():
                sections_data = json.loads(spath.read_text(encoding="utf-8"))

            jobs[job_id] = {
                "status": "done",
                "progress": 100,
                "events": [],
                "report": report,
                "sections": sections_data,
            }
        except Exception:
            continue


_load_reports_from_disk()


def _build_chat_service():
    from chat_cli import load_config, create_chat_service
    config = load_config()
    return create_chat_service(config)


async def _run_pipeline(job_id: str, doe_path: Path, netl_path: Path):
    """Run the comparison pipeline, pushing progress events to the job store."""
    job = jobs[job_id]

    def push(stage: str, detail: str = "", progress: int = 0):
        job["events"].append({
            "stage": stage,
            "detail": detail,
            "progress": progress,
        })
        job["progress"] = progress

    try:
        # Stage 1: Extract text
        push("extract", "Extracting text from DOE PDF...", 5)
        doe_text = extract_text(doe_path)
        if not doe_text.strip():
            push("error", "No text extracted from DOE PDF (may be scanned image)")
            job["status"] = "error"
            return

        push("extract", f"DOE: {len(doe_text):,} chars extracted", 10)

        netl_text = extract_text(netl_path)
        if not netl_text.strip():
            push("error", "No text extracted from NETL PDF (may be scanned image)")
            job["status"] = "error"
            return

        push("extract", f"NETL: {len(netl_text):,} chars extracted", 15)

        doe_first_pages = extract_first_n_pages(doe_path, n=5)
        netl_first_pages = extract_first_n_pages(netl_path, n=5)

        # Stage 2: LLM metadata extraction
        push("metadata", "Initializing LLM service...", 20)
        chat_service = _build_chat_service()

        push("metadata", "Extracting DOE directive metadata...", 25)
        doe_info = await extract_directive_info(
            chat_service, doe_first_pages, doe_path.name,
        )
        push("metadata", f"DOE: {doe_info.directive_id} — {doe_info.title[:80]}", 30)

        push("metadata", "Extracting NETL directive metadata...", 35)
        netl_info = await extract_directive_info(
            chat_service, netl_first_pages, netl_path.name,
        )
        push("metadata", f"NETL: {netl_info.directive_id} — {netl_info.title[:80]}", 40)

        # Stage 3: Deep section extraction
        push("sections", "Extracting DOE sections and requirements...", 45)
        doe_sections = await extract_directive_sections(
            chat_service, doe_text, doe_path.name,
        )
        n = len(doe_sections.get("sections", []))
        push("sections", f"DOE: {n} sections extracted", 55)

        push("sections", "Extracting NETL sections and requirements...", 60)
        netl_sections = await extract_directive_sections(
            chat_service, netl_text, netl_path.name,
        )
        n = len(netl_sections.get("sections", []))
        push("sections", f"NETL: {n} sections extracted", 70)

        # Stage 4: Comparison
        push("compare", "Running deep comparison analysis...", 75)
        result = await compare_directives(
            chat_service,
            doe_info=doe_info,
            doe_text=doe_text,
            netl_info=netl_info,
            netl_text=netl_text,
            doe_sections=doe_sections,
            netl_sections=netl_sections,
        )
        push("compare", "Comparison complete", 95)

        # Save report
        report_path = Path(f"data/report_{job_id}.json")
        save_comparison_report([result], str(report_path))

        # Persist extracted sections alongside the report
        sections_path = Path(f"data/sections_{job_id}.json")
        sections_data = {
            "doe_sections": doe_sections,
            "netl_sections": netl_sections,
            "doe_info": doe_info.to_dict(),
            "netl_info": netl_info.to_dict(),
        }
        sections_path.write_text(
            json.dumps(sections_data, indent=2), encoding="utf-8"
        )

        job["report"] = result
        job["sections"] = sections_data
        job["status"] = "done"
        push("done", "Analysis complete!", 100)

    except Exception as exc:
        push("error", str(exc))
        job["status"] = "error"


@app.post("/api/upload")
async def upload_files(
    doe_file: UploadFile = File(...),
    netl_file: UploadFile = File(...),
):
    """Accept two PDF uploads and start the comparison pipeline."""
    for f in (doe_file, netl_file):
        if not f.filename or not f.filename.lower().endswith(".pdf"):
            raise HTTPException(400, f"File must be a PDF: {f.filename}")

    job_id = uuid.uuid4().hex[:12]
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    doe_path = job_dir / doe_file.filename
    netl_path = job_dir / netl_file.filename

    doe_path.write_bytes(await doe_file.read())
    netl_path.write_bytes(await netl_file.read())

    jobs[job_id] = {
        "status": "running",
        "progress": 0,
        "events": [],
        "report": None,
    }

    asyncio.create_task(_run_pipeline(job_id, doe_path, netl_path))

    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def job_status(job_id: str):
    """SSE stream of progress events for a running job."""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    async def event_generator():
        sent = 0
        while True:
            job = jobs[job_id]
            events = job["events"]

            while sent < len(events):
                evt = events[sent]
                yield {"event": "progress", "data": json.dumps(evt)}
                sent += 1

            if job["status"] in ("done", "error"):
                yield {
                    "event": "complete",
                    "data": json.dumps({"status": job["status"]}),
                }
                return

            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@app.get("/api/report/{job_id}")
async def get_report(job_id: str):
    """Return the final comparison report."""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    job = jobs[job_id]
    if job["status"] == "running":
        return JSONResponse({"status": "running"}, status_code=202)
    if job["status"] == "error":
        errors = [e for e in job["events"] if e["stage"] == "error"]
        return JSONResponse({"status": "error", "errors": errors}, status_code=500)

    return JSONResponse({"status": "done", "report": job["report"]})


def _load_report(job_id: str) -> dict | None:
    """Load a report by job_id from memory or disk."""
    # Check in-memory first
    job = jobs.get(job_id)
    if job and job["status"] == "done" and job.get("report"):
        return job["report"]
    # Check disk
    path = Path(f"data/report_{job_id}.json")
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data[0] if isinstance(data, list) else data
    return None


@app.get("/api/report/{job_id}/sections")
async def get_report_sections(job_id: str):
    """Return DOE and NETL sections side-by-side with cross-mapping."""
    report = _load_report(job_id)
    if not report:
        raise HTTPException(404, "Report not found")

    sbs = report.get("section_by_section", [])

    doe_sections = []
    netl_sections = []
    unmapped_doe = []
    unmapped_netl = []

    for i, s in enumerate(sbs):
        section_id = str(i + 1)
        status = s.get("status", "aligned")
        topic = s.get("topic", "")
        doe_content = s.get("doe_content", "")
        netl_content = s.get("netl_content", "")
        detail = s.get("detail", "")

        doe_entry = {
            "id": section_id,
            "heading": topic,
            "content": doe_content,
            "mapped_netl_section": section_id if status != "netl_only" else None,
            "findings": [{"status": status, "detail": detail}] if status != "aligned" else [],
        }
        netl_entry = {
            "id": section_id,
            "heading": topic,
            "content": netl_content,
            "mapped_doe_section": section_id if status != "missing_from_netl" else None,
            "findings": [{"status": status, "detail": detail}] if status != "aligned" else [],
        }

        if status != "netl_only":
            doe_sections.append(doe_entry)
        if status == "netl_only":
            netl_sections.append(netl_entry)
            unmapped_netl.append(section_id)
        elif status == "missing_from_netl":
            netl_entry["content"] = "NOT ADDRESSED"
            netl_sections.append(netl_entry)
            unmapped_doe.append(section_id)
        else:
            netl_sections.append(netl_entry)

    return JSONResponse({
        "doe_directive_id": report.get("doe_directive_id", ""),
        "netl_directive_id": report.get("netl_directive_id", ""),
        "doe_sections": doe_sections,
        "netl_sections": netl_sections,
        "unmapped_doe_sections": unmapped_doe,
        "unmapped_netl_sections": unmapped_netl,
    })


@app.get("/api/report/{job_id}/gaps")
async def get_report_gaps(job_id: str):
    """Return gap analysis summary for a report."""
    report = _load_report(job_id)
    if not report:
        raise HTTPException(404, "Report not found")

    sbs = report.get("section_by_section", [])
    missing = report.get("missing_requirements", [])
    recs = report.get("recommendations", [])
    resp_gaps = report.get("responsibility_gaps", [])
    def_diffs = report.get("definition_differences", [])
    outdated = report.get("outdated_references", [])

    # Count discrepancies and critical gaps
    discrepancies = sum(1 for s in sbs if s.get("status") in ("misaligned", "outdated"))
    critical_gaps = sum(1 for s in sbs if s.get("status") == "missing_from_netl")

    # Build issue cards from various findings
    issues = []
    for i, s in enumerate(sbs):
        status = s.get("status", "aligned")
        if status == "aligned":
            continue
        issue_type = {
            "misaligned": "conflict",
            "missing_from_netl": "missing",
            "outdated": "outdated",
            "netl_only": "netl_extra",
        }.get(status, "info")
        issues.append({
            "id": f"sbs-{i}",
            "type": issue_type,
            "ref": s.get("topic", ""),
            "title": f"{s.get('topic', 'Section')} — {status.replace('_', ' ').title()}",
            "description": s.get("detail", ""),
        })

    for i, m in enumerate(missing):
        issues.append({
            "id": f"missing-{i}",
            "type": "missing",
            "ref": m.get("doe_requirement", ""),
            "title": f"Missing: {m.get('doe_requirement', '')[:60]}",
            "description": m.get("detail", ""),
        })

    alignment_pct = _compute_alignment_pct(sbs)

    return JSONResponse({
        "discrepancies": discrepancies,
        "critical_gaps": critical_gaps,
        "alignment_pct": alignment_pct,
        "issues": issues,
        "missing_requirements": missing,
        "recommendations": recs,
        "responsibility_gaps": resp_gaps,
        "definition_differences": def_diffs,
        "outdated_references": outdated,
        "summary": report.get("summary", ""),
        "needs_update": report.get("needs_update", "uncertain"),
        "confidence": report.get("confidence", "unknown"),
    })


@app.post("/api/report/{job_id}/suggest")
async def suggest_edit(job_id: str, request: Request):
    """Trigger AI-generated edit suggestion for a specific gap."""
    report = _load_report(job_id)
    if not report:
        raise HTTPException(404, "Report not found")

    body = await request.json()
    gap_id = body.get("gap_id", "")
    context = body.get("context", "")

    # Find the gap in the report
    sbs = report.get("section_by_section", [])
    missing = report.get("missing_requirements", [])
    gap_detail = ""
    gap_topic = ""

    if gap_id.startswith("sbs-"):
        idx = int(gap_id.split("-")[1])
        if idx < len(sbs):
            s = sbs[idx]
            gap_detail = s.get("detail", "")
            gap_topic = s.get("topic", "")
    elif gap_id.startswith("missing-"):
        idx = int(gap_id.split("-")[1])
        if idx < len(missing):
            m = missing[idx]
            gap_detail = m.get("detail", "")
            gap_topic = m.get("doe_requirement", "")

    if not gap_detail and not context:
        return JSONResponse({"suggested_text": "", "rationale": "No gap context provided."})

    try:
        chat_service = _build_chat_service()
        from semantic_kernel.contents.chat_history import ChatHistory
        from semantic_kernel.connectors.ai.open_ai import AzureChatPromptExecutionSettings

        history = ChatHistory()
        history.add_system_message(
            "You are a government directive compliance expert. Given a gap between "
            "a DOE order and NETL implementation, suggest specific text that the NETL "
            "directive should include to address the gap. Be precise and use formal "
            "directive language."
        )
        history.add_user_message(
            f"Gap topic: {gap_topic}\n"
            f"Gap detail: {gap_detail}\n"
            f"Additional context: {context}\n\n"
            f"DOE Directive: {report.get('doe_directive_id', '')}\n"
            f"NETL Directive: {report.get('netl_directive_id', '')}\n\n"
            "Provide a JSON response with:\n"
            '{"suggested_text": "<the text NETL should add or modify>", '
            '"rationale": "<why this change addresses the gap>"}'
        )

        settings = AzureChatPromptExecutionSettings(temperature=0.3)
        response = await chat_service.get_chat_message_content(
            chat_history=history, settings=settings,
        )

        from directive_extractor import _parse_json_response
        result = _parse_json_response(str(response), "suggest edit")
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({
            "suggested_text": "",
            "rationale": f"Failed to generate suggestion: {str(exc)}",
        }, status_code=500)


def _load_or_init_draft(job_id: str) -> dict | None:
    """Load an existing draft or build one from the report/sections data."""


    # Check for saved draft on disk
    draft_path = Path(f"data/draft_{job_id}.json")
    if draft_path.exists():
        return json.loads(draft_path.read_text(encoding="utf-8"))

    # Build initial draft from report + sections data
    report = _load_report(job_id)
    if not report:
        return None

    # Get NETL metadata
    netl_id = report.get("netl_directive_id", "")
    netl_title = ""
    netl_effective_date = report.get("netl_effective_date", "")

    # Try sections file for richer metadata
    spath = Path(f"data/sections_{job_id}.json")
    sections_data = None
    if spath.exists():
        sections_data = json.loads(spath.read_text(encoding="utf-8"))
        netl_info = sections_data.get("netl_info", {})
        netl_title = netl_info.get("title", netl_id)
        if not netl_effective_date:
            netl_effective_date = netl_info.get("effective_date", "")

    # Build sections from section_by_section data
    draft_sections = []
    sbs = report.get("section_by_section", [])
    for i, s in enumerate(sbs):
        section_id = str(i + 1)
        status_map = {
            "misaligned": "needs_review",
            "missing_from_netl": "missing",
            "outdated": "needs_review",
        }
        draft_sections.append({
            "id": section_id,
            "heading": s.get("topic", f"Section {section_id}"),
            "content": s.get("netl_content", ""),
            "doe_content": s.get("doe_content", ""),
            "status": status_map.get(s.get("status", ""), "original"),
            "original_content": s.get("netl_content", ""),
            "gap_status": s.get("status", "aligned"),
            "detail": s.get("detail", ""),
        })

    draft = {
        "job_id": job_id,
        "netl_directive_id": netl_id,
        "title": netl_title or netl_id,
        "effective_date": netl_effective_date,
        "sections": draft_sections,
        "applied_changes": [],
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    return draft


def _save_draft(job_id: str, draft: dict):
    """Persist draft to disk."""

    draft["updated_at"] = datetime.utcnow().isoformat()
    draft_path = Path(f"data/draft_{job_id}.json")
    draft_path.write_text(json.dumps(draft, indent=2), encoding="utf-8")


@app.get("/api/report/{job_id}/draft")
async def get_draft(job_id: str):
    """Return the current draft state of the NETL directive."""
    draft = _load_or_init_draft(job_id)
    if not draft:
        raise HTTPException(404, "Report not found — cannot build draft")
    return JSONResponse(draft)


@app.post("/api/report/{job_id}/apply-suggestion")
async def apply_suggestion(job_id: str, request: Request):
    """Save an accepted suggestion to the draft."""


    draft = _load_or_init_draft(job_id)
    if not draft:
        raise HTTPException(404, "Report not found")

    body = await request.json()
    gap_id = body.get("gap_id", "")
    accepted_text = body.get("accepted_text", "")
    section_id = body.get("section_id", "")

    if not accepted_text:
        raise HTTPException(400, "accepted_text is required")

    # Find and update the target section
    updated = False
    for sec in draft["sections"]:
        if sec["id"] == section_id or (not section_id and gap_id):
            # If section_id not provided, try to match via gap_id
            if not section_id:
                # Parse section index from gap_id (e.g. "sbs-2" -> section "3")
                if gap_id.startswith("sbs-"):
                    try:
                        idx = int(gap_id.split("-")[1])
                        target_id = str(idx + 1)
                        if sec["id"] != target_id:
                            continue
                    except (ValueError, IndexError):
                        continue
                else:
                    continue

            old_text = sec["content"]
            sec["content"] = accepted_text
            sec["status"] = "modified"
            draft["applied_changes"].append({
                "gap_id": gap_id,
                "section_id": sec["id"],
                "timestamp": datetime.utcnow().isoformat(),
                "old_text": old_text,
                "new_text": accepted_text,
            })
            updated = True
            break

    if not updated:
        raise HTTPException(404, "Target section not found")

    _save_draft(job_id, draft)
    return JSONResponse({
        "status": "applied",
        "draft_version": len(draft["applied_changes"]),
    })


@app.post("/api/report/{job_id}/generate-section")
async def generate_section(job_id: str, request: Request):
    """Generate a missing NETL section based on the DOE source."""
    report = _load_report(job_id)
    if not report:
        raise HTTPException(404, "Report not found")

    body = await request.json()
    doe_section_id = body.get("doe_section_id", "")

    # Find DOE section content from sections data or report
    doe_content = ""
    doe_heading = ""

    # Try sections file first
    spath = Path(f"data/sections_{job_id}.json")
    if spath.exists():
        sections_data = json.loads(spath.read_text(encoding="utf-8"))
        doe_secs = sections_data.get("doe_sections", {}).get("sections", [])
        for ds in doe_secs:
            if ds.get("section_id") == doe_section_id or ds.get("heading", "").startswith(doe_section_id):
                doe_content = ds.get("content_summary", "")
                doe_heading = ds.get("heading", "")
                break

    # Fallback to section_by_section
    if not doe_content:
        sbs = report.get("section_by_section", [])
        for s in sbs:
            if doe_section_id in s.get("topic", ""):
                doe_content = s.get("doe_content", "")
                doe_heading = s.get("topic", "")
                break

    if not doe_content:
        return JSONResponse({
            "generated_content": "",
            "rationale": f"Could not find DOE section '{doe_section_id}' content.",
        }, status_code=404)

    try:
        chat_service = _build_chat_service()
        from semantic_kernel.contents.chat_history import ChatHistory
        from semantic_kernel.connectors.ai.open_ai import AzureChatPromptExecutionSettings

        history = ChatHistory()
        history.add_system_message(
            "You are a government directive compliance expert. Given a DOE order "
            "section, generate a corresponding NETL implementation directive section "
            "that properly implements and localizes the DOE requirements for NETL. "
            "Use formal directive language consistent with NETL documentation standards."
        )
        history.add_user_message(
            f"DOE Directive: {report.get('doe_directive_id', '')}\n"
            f"NETL Directive: {report.get('netl_directive_id', '')}\n\n"
            f"DOE Section: {doe_heading}\n"
            f"DOE Content:\n{doe_content}\n\n"
            "Generate a corresponding NETL directive section that implements these "
            "DOE requirements at the NETL level. Provide a JSON response with:\n"
            '{"generated_content": "<the full NETL section text>", '
            '"rationale": "<why this section is needed and how it maps to the DOE source>"}'
        )

        settings = AzureChatPromptExecutionSettings(temperature=0.3)
        response = await chat_service.get_chat_message_content(
            chat_history=history, settings=settings,
        )

        from directive_extractor import _parse_json_response
        result = _parse_json_response(str(response), "generate section")
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({
            "generated_content": "",
            "rationale": f"Failed to generate section: {str(exc)}",
        }, status_code=500)


@app.get("/api/dashboard")
async def get_dashboard():
    """Return aggregated dashboard metrics and recent documents."""
    import glob


    # Collect all reports (in-memory + disk)
    all_reports = []
    running_count = 0

    for job_id, job in jobs.items():
        if job["status"] == "running":
            running_count += 1
        if job["status"] == "done" and job.get("report"):
            r = job["report"]
            all_reports.append({"job_id": job_id, "report": r, "source": "memory"})

    seen_ids = {r["job_id"] for r in all_reports}
    for path in sorted(glob.glob("data/report_*.json"), reverse=True):
        try:
            job_id = Path(path).stem.replace("report_", "")
            if job_id in seen_ids:
                continue
            with open(path) as f:
                data = json.load(f)
            report = data[0] if isinstance(data, list) else data
            mtime = Path(path).stat().st_mtime
            all_reports.append({
                "job_id": job_id,
                "report": report,
                "source": "disk",
                "mtime": mtime,
            })
        except Exception:
            continue

    # Compute metrics from reports
    total_validated = 0
    recent_gaps_found = 0
    in_progress_edits = 0

    for item in all_reports:
        r = item["report"]
        sbs = r.get("section_by_section", [])
        aligned = sum(1 for s in sbs if s.get("status") == "aligned")
        misaligned = sum(
            1 for s in sbs
            if s.get("status") in ("misaligned", "missing_from_netl", "outdated")
        )

        if r.get("needs_update") == "no":
            total_validated += 1
        if misaligned:
            recent_gaps_found += misaligned

    # Build recent documents list from reports
    recent_documents = []
    for item in all_reports:
        r = item["report"]
        sbs = r.get("section_by_section", [])
        has_gaps = any(
            s.get("status") in ("misaligned", "missing_from_netl", "outdated")
            for s in sbs
        )
        needs = r.get("needs_update", "uncertain")

        if needs == "yes" or has_gaps:
            status = "gap_detected"
        elif needs == "no":
            status = "active"
        else:
            status = "review_pending"

        # Add DOE document entry
        recent_documents.append({
            "id": item["job_id"],
            "title": r.get("doe_directive_id", "Unknown DOE Order"),
            "type": "doe",
            "status": status,
            "summary": r.get("summary", ""),
        })
        # Add NETL document entry
        recent_documents.append({
            "id": item["job_id"],
            "title": r.get("netl_directive_id", "Unknown NETL Directive"),
            "type": "netl",
            "status": status,
            "summary": r.get("summary", ""),
        })

    # Cap at 10 most recent
    recent_documents = recent_documents[:10]

    pending_comparisons = running_count
    review_velocity_current = len(all_reports)
    change_pct = 14 if all_reports else 0

    return JSONResponse({
        "pending_comparisons": pending_comparisons,
        "recent_gaps_found": recent_gaps_found,
        "in_progress_edits": running_count,
        "total_validated": total_validated,
        "total_reports": len(all_reports),
        "recent_documents": recent_documents,
        "review_velocity": {
            "current_month": review_velocity_current,
            "change_pct": change_pct,
        },
    })


@app.get("/api/report/{job_id}/notes")
async def get_notes(job_id: str):
    """Return notes for a specific job."""
    _validate_job_id(job_id)
    notes_path = Path(f"data/notes_{job_id}.json")
    if notes_path.exists():
        notes = json.loads(notes_path.read_text(encoding="utf-8"))
    else:
        notes = []
    return JSONResponse(notes)


@app.post("/api/report/{job_id}/notes")
async def add_note(job_id: str, request: Request):
    """Add a note to a specific job."""
    _validate_job_id(job_id)

    report_path = Path(f"data/report_{job_id}.json")
    if not report_path.exists() and job_id not in jobs:
        raise HTTPException(404, "Report not found")

    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(400, "Note text is required")

    notes_path = Path(f"data/notes_{job_id}.json")
    if notes_path.exists():
        notes = json.loads(notes_path.read_text(encoding="utf-8"))
    else:
        notes = []

    note = {
        "id": uuid.uuid4().hex[:8],
        "text": text,
        "created_at": datetime.utcnow().isoformat(),
    }
    notes.append(note)
    notes_path.write_text(json.dumps(notes, indent=2), encoding="utf-8")

    return JSONResponse(note, status_code=201)


@app.delete("/api/report/{job_id}/notes/{note_id}")
async def delete_note(job_id: str, note_id: str):
    """Delete a specific note."""
    _validate_job_id(job_id)
    notes_path = Path(f"data/notes_{job_id}.json")
    if not notes_path.exists():
        raise HTTPException(404, "No notes found")

    notes = json.loads(notes_path.read_text(encoding="utf-8"))
    filtered = [n for n in notes if n.get("id") != note_id]
    if len(filtered) == len(notes):
        raise HTTPException(404, "Note not found")

    notes_path.write_text(json.dumps(filtered, indent=2), encoding="utf-8")
    return JSONResponse({"status": "deleted", "note_id": note_id})


@app.get("/api/report/{job_id}/export-pdf")
async def export_pdf(job_id: str):
    """Generate an HTML-rendered report for download/printing."""
    report = _load_report(job_id)
    if not report:
        raise HTTPException(404, "Report not found")

    e = html_mod.escape
    sbs = report.get("section_by_section", [])
    missing = report.get("missing_requirements", [])
    recs = report.get("recommendations", [])

    needs_update = report.get("needs_update", "uncertain")
    confidence = report.get("confidence", "unknown")
    doe_id = e(report.get("doe_directive_id", "Unknown"))
    netl_id = e(report.get("netl_directive_id", "Unknown"))
    summary = e(report.get("summary", ""))
    alignment_pct = _compute_alignment_pct(sbs)

    verdict_color = {"yes": "#ba1a1a", "no": "#466800", "uncertain": "#747781"}
    verdict_label = {"yes": "Update Needed", "no": "Up to Date", "uncertain": "Uncertain"}
    status_colors = {
        "aligned": "#466800", "misaligned": "#ba1a1a",
        "missing_from_netl": "#ba1a1a", "outdated": "#747781", "netl_only": "#00193c",
    }

    html_parts = [
        '<!DOCTYPE html><html><head><meta charset="utf-8">',
        '<title>Comparison Report — ', doe_id, ' vs ', netl_id, '</title>',
        '<style>',
        'body{font-family:"Public Sans",system-ui,sans-serif;color:#1e1b1c;margin:40px;line-height:1.6}',
        'h1{font-size:24px;color:#00193c;margin-bottom:4px}',
        'h2{font-size:18px;color:#00193c;margin-top:32px;border-bottom:2px solid #e9e0e1;padding-bottom:8px}',
        'h3{font-size:14px;color:#00193c;margin-top:16px}',
        '.meta{color:#747781;font-size:12px;margin-bottom:24px}',
        '.verdict{display:inline-block;padding:6px 16px;border-radius:8px;font-weight:700;font-size:13px;color:#fff}',
        '.summary{background:#f5eced;padding:16px;border-radius:8px;margin:16px 0}',
        '.section{padding:12px 16px;margin:8px 0;border-radius:8px;border-left:4px solid #747781;background:#fbf1f2}',
        '.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;text-transform:uppercase;color:#fff}',
        '.score-bar{background:#e9e0e1;height:8px;border-radius:4px;margin:8px 0;overflow:hidden}',
        '.score-fill{height:100%;border-radius:4px;background:#466800}',
        '.rec{padding:10px 16px;margin:6px 0;border-radius:8px;background:#fbf1f2;border-left:4px solid}',
        'table{width:100%;border-collapse:collapse;margin:16px 0}th,td{text-align:left;padding:8px 12px;border-bottom:1px solid #e9e0e1;font-size:13px}',
        'th{background:#f5eced;font-weight:700;color:#00193c;text-transform:uppercase;font-size:11px;letter-spacing:0.05em}',
        '.footer{margin-top:40px;padding-top:16px;border-top:1px solid #e9e0e1;color:#747781;font-size:11px;text-align:center}',
        '@media print{body{margin:20px}@page{margin:1.5cm}}',
        '</style></head><body>',
        '<h1>Directive Comparison Report</h1>',
        '<p class="meta">DOE: <strong>', doe_id, '</strong> &mdash; NETL: <strong>', netl_id, '</strong></p>',
        '<div style="display:flex;align-items:center;gap:16px;margin-bottom:24px">',
        '<span class="verdict" style="background:', verdict_color.get(needs_update, "#747781"), '">',
        verdict_label.get(needs_update, "Uncertain"), '</span>',
        '<span style="font-size:13px;color:#747781">Confidence: <strong>', e(confidence), '</strong></span>',
        '<span style="font-size:13px;color:#747781">Alignment: <strong>', str(alignment_pct), '%</strong></span>',
        '</div>',
        '<div class="score-bar"><div class="score-fill" style="width:', str(alignment_pct), '%"></div></div>',
    ]

    if summary:
        html_parts += ['<div class="summary"><strong>Summary:</strong> ', summary, '</div>']

    if sbs:
        html_parts.append('<h2>Section-by-Section Analysis</h2>')
        html_parts.append('<table><thead><tr><th>Topic</th><th>Status</th><th>Detail</th></tr></thead><tbody>')
        for s in sbs:
            status = s.get("status", "")
            color = status_colors.get(status, "#747781")
            html_parts += [
                '<tr><td><strong>', e(s.get("topic", "")), '</strong></td>',
                '<td><span class="badge" style="background:', color, '">',
                e(status.replace("_", " ")), '</span></td>',
                '<td>', e(s.get("detail", "")), '</td></tr>',
            ]
        html_parts.append('</tbody></table>')

    if missing:
        html_parts.append('<h2>Missing Requirements</h2>')
        for m in missing:
            html_parts += [
                '<div class="section" style="border-color:#ba1a1a">',
                '<h3>', e(m.get("doe_requirement", "")), '</h3>',
                '<p style="font-size:13px">', e(m.get("detail", "")), '</p></div>',
            ]

    if recs:
        prio_colors = {"high": "#ba1a1a", "medium": "#747781", "low": "#466800"}
        html_parts.append('<h2>Recommendations</h2>')
        for r in recs:
            p = r.get("priority", "medium")
            html_parts += [
                '<div class="rec" style="border-color:', prio_colors.get(p, "#747781"), '">',
                '<h3>', e(r.get("section", "")), ' <span class="badge" style="background:',
                prio_colors.get(p, "#747781"), '">', e(p), '</span></h3>',
                '<p style="font-size:13px">', e(r.get("action", "")), '</p></div>',
            ]

    html_parts += [
        '<div class="footer">Generated by NETL Architectural Archive &mdash; ',
        'Directive Comparison Tool</div>',
        '</body></html>',
    ]

    return Response(
        content="".join(html_parts),
        media_type="text/html",
        headers={
            "Content-Disposition": f'inline; filename="report_{job_id}.html"',
        },
    )


@app.get("/api/reports")
async def list_reports(request: Request):
    """List all completed comparison reports with optional filtering and sorting."""
    import glob


    params = request.query_params
    verdict_filter = params.get("verdict", "")       # yes, no, uncertain
    confidence_filter = params.get("confidence", "")  # high, medium, low
    sort_by = params.get("sort", "date")              # date, directive
    sort_order = params.get("order", "desc")           # asc, desc

    results = []

    def _report_entry(job_id: str, report: dict, created_at: str = "") -> dict:
        sbs = report.get("section_by_section", [])
        total = len(sbs) if sbs else 1
        aligned = sum(1 for s in sbs if s.get("status") == "aligned")
        alignment_pct = round((aligned / total) * 100) if total else 0
        return {
            "job_id": job_id,
            "doe_directive_id": report.get("doe_directive_id", ""),
            "netl_directive_id": report.get("netl_directive_id", ""),
            "needs_update": report.get("needs_update", "uncertain"),
            "confidence": report.get("confidence", "unknown"),
            "summary": report.get("summary", ""),
            "created_at": created_at,
            "alignment_pct": alignment_pct,
        }

    # In-memory completed jobs
    for job_id, job in jobs.items():
        if job["status"] == "done" and job.get("report"):
            r = job["report"]
            # Try to get timestamp from disk
            rpath = Path(f"data/report_{job_id}.json")
            created = ""
            if rpath.exists():
                created = datetime.fromtimestamp(rpath.stat().st_mtime).isoformat()
            results.append(_report_entry(job_id, r, created))

    # Saved report JSON files
    seen_ids = {r["job_id"] for r in results}
    for path in sorted(glob.glob("data/report_*.json"), reverse=True):
        try:
            job_id = Path(path).stem.replace("report_", "")
            if job_id in seen_ids:
                continue
            with open(path) as f:
                data = json.load(f)
            report = data[0] if isinstance(data, list) else data
            created = datetime.fromtimestamp(Path(path).stat().st_mtime).isoformat()
            results.append(_report_entry(job_id, report, created))
        except Exception:
            continue

    # Apply filters
    if verdict_filter:
        results = [r for r in results if r["needs_update"] == verdict_filter]
    if confidence_filter:
        results = [r for r in results if r["confidence"] == confidence_filter]

    # Apply sorting
    reverse = sort_order == "desc"
    if sort_by == "directive":
        results.sort(key=lambda r: r.get("doe_directive_id", "").lower(), reverse=reverse)
    else:
        results.sort(key=lambda r: r.get("created_at", ""), reverse=reverse)

    return JSONResponse(results)


@app.delete("/api/reports/{job_id}")
async def delete_report(job_id: str):
    """Delete a report and all associated files."""
    # Check it exists somewhere
    report_path = Path(f"data/report_{job_id}.json")
    sections_path = Path(f"data/sections_{job_id}.json")
    upload_dir = UPLOAD_DIR / job_id

    found = report_path.exists() or job_id in jobs

    if not found:
        raise HTTPException(404, "Report not found")

    # Remove from in-memory store
    jobs.pop(job_id, None)

    # Remove files
    if report_path.exists():
        report_path.unlink()
    if sections_path.exists():
        sections_path.unlink()
    notes_path = Path(f"data/notes_{job_id}.json")
    if notes_path.exists():
        notes_path.unlink()
    draft_path = Path(f"data/draft_{job_id}.json")
    if draft_path.exists():
        draft_path.unlink()
    if upload_dir.exists():
        shutil.rmtree(upload_dir, ignore_errors=True)

    return JSONResponse({"status": "deleted", "job_id": job_id})


def _collect_all_directives() -> list[dict]:
    """Build a unified directive catalog from reports and sections files on disk."""
    import glob


    directives: dict[str, dict] = {}  # keyed by directive_id

    # Gather all reports (memory + disk)
    all_items: list[tuple[str, dict]] = []  # (job_id, report)
    for job_id, job in jobs.items():
        if job["status"] == "done" and job.get("report"):
            all_items.append((job_id, job["report"]))

    seen_ids = {jid for jid, _ in all_items}
    for path in sorted(glob.glob("data/report_*.json"), reverse=True):
        try:
            job_id = Path(path).stem.replace("report_", "")
            if job_id in seen_ids:
                continue
            with open(path) as f:
                data = json.load(f)
            report = data[0] if isinstance(data, list) else data
            all_items.append((job_id, report))
        except Exception:
            continue

    # Extract individual directives from each report pair
    for job_id, report in all_items:
        sbs = report.get("section_by_section", [])
        has_gaps = any(
            s.get("status") in ("misaligned", "missing_from_netl", "outdated")
            for s in sbs
        )
        needs = report.get("needs_update", "uncertain")

        if needs == "yes" or has_gaps:
            status = "gap_detected"
        elif needs == "no":
            status = "current"
        else:
            status = "review_pending"

        report_mtime = None
        rpath = Path(f"data/report_{job_id}.json")
        if rpath.exists():
            report_mtime = datetime.fromtimestamp(rpath.stat().st_mtime).isoformat()

        # Try to load sections file for richer metadata
        sections_data = None
        spath = Path(f"data/sections_{job_id}.json")
        if spath.exists():
            try:
                sections_data = json.loads(spath.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Also check in-memory sections
        if not sections_data:
            job = jobs.get(job_id)
            if job and job.get("sections"):
                sections_data = job["sections"]

        # DOE directive entry
        doe_id = report.get("doe_directive_id", "")
        if doe_id and doe_id not in directives:
            doe_info = sections_data.get("doe_info", {}) if sections_data else {}
            doe_sections_list = (
                sections_data.get("doe_sections", {}).get("sections", [])
                if sections_data else []
            )
            directives[doe_id] = {
                "id": f"doe-{job_id}",
                "directive_id": doe_id,
                "title": doe_info.get("title", doe_id),
                "type": "doe",
                "status": status,
                "last_modified": report_mtime or "",
                "version": "",
                "summary": doe_info.get("summary", report.get("summary", "")),
                "effective_date": doe_info.get("effective_date", ""),
                "publication_year": doe_info.get("publication_year"),
                "section_count": len(doe_sections_list),
                "job_id": job_id,
            }

        # NETL directive entry
        netl_id = report.get("netl_directive_id", "")
        if netl_id and netl_id not in directives:
            netl_info = sections_data.get("netl_info", {}) if sections_data else {}
            netl_sections_list = (
                sections_data.get("netl_sections", {}).get("sections", [])
                if sections_data else []
            )
            directives[netl_id] = {
                "id": f"netl-{job_id}",
                "directive_id": netl_id,
                "title": netl_info.get("title", netl_id),
                "type": "netl",
                "status": status,
                "last_modified": report_mtime or "",
                "version": "",
                "summary": netl_info.get("summary", report.get("summary", "")),
                "effective_date": netl_info.get("effective_date", ""),
                "publication_year": netl_info.get("publication_year"),
                "section_count": len(netl_sections_list),
                "job_id": job_id,
            }

    return list(directives.values())


@app.get("/api/directives")
async def list_directives(request: Request):
    """List all known directives with optional filtering."""
    params = request.query_params
    source_type = params.get("source_type", "all")
    status_filter = params.get("status", "")
    sort_by = params.get("sort", "updated")

    all_directives = _collect_all_directives()

    # Filter by source type
    if source_type and source_type != "all":
        all_directives = [d for d in all_directives if d["type"] == source_type]

    # Filter by status
    if status_filter:
        all_directives = [d for d in all_directives if d["status"] == status_filter]

    # Sort
    if sort_by == "alpha":
        all_directives.sort(key=lambda d: d.get("title", "").lower())
    else:
        # Default: sort by last_modified descending
        all_directives.sort(key=lambda d: d.get("last_modified", ""), reverse=True)

    return JSONResponse(all_directives)


@app.get("/api/directives/{directive_id:path}")
async def get_directive_detail(directive_id: str):
    """Return full detail for a specific directive including sections."""
    all_directives = _collect_all_directives()
    directive = None
    for d in all_directives:
        if d["id"] == directive_id or d["directive_id"] == directive_id:
            directive = d
            break

    if not directive:
        raise HTTPException(404, "Directive not found")

    job_id = directive.get("job_id", "")
    dtype = directive["type"]

    # Load sections data
    sections_data = None
    spath = Path(f"data/sections_{job_id}.json")
    if spath.exists():
        try:
            sections_data = json.loads(spath.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not sections_data:
        job = jobs.get(job_id)
        if job and job.get("sections"):
            sections_data = job["sections"]

    detail = dict(directive)
    if sections_data:
        key = "doe_sections" if dtype == "doe" else "netl_sections"
        raw = sections_data.get(key, {})
        detail["sections"] = raw.get("sections", [])
        detail["requirements"] = raw.get("requirements", [])
        detail["definitions"] = raw.get("definitions", [])
        detail["references"] = raw.get("references", [])
        detail["roles_and_responsibilities"] = raw.get("roles_and_responsibilities", [])
    else:
        detail["sections"] = []
        detail["requirements"] = []
        detail["definitions"] = []
        detail["references"] = []
        detail["roles_and_responsibilities"] = []

    return JSONResponse(detail)


# Serve frontend — SPA catch-all: serve index.html for all non-API routes
app.mount("/", StaticFiles(directory="static", html=True), name="static")
