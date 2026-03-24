"""FastAPI backend for DOE/NETL directive comparison.

Provides:
- POST /api/upload  — accept DOE + NETL PDF uploads, start comparison job
- GET  /api/status/{job_id} — SSE stream of progress updates
- GET  /api/report/{job_id} — final comparison report JSON
- GET  / — serves the frontend
"""

import asyncio
import json
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
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

    # Compute alignment score
    total_sections = len(sbs) if sbs else 1
    aligned_count = sum(1 for s in sbs if s.get("status") == "aligned")
    alignment_pct = round((aligned_count / total_sections) * 100)

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


@app.get("/api/reports")
async def list_reports():
    """List all completed comparison reports (in-memory + saved JSON files)."""
    import glob

    results = []

    # In-memory completed jobs
    for job_id, job in jobs.items():
        if job["status"] == "done" and job.get("report"):
            r = job["report"]
            results.append({
                "job_id": job_id,
                "doe_directive_id": r.get("doe_directive_id", ""),
                "netl_directive_id": r.get("netl_directive_id", ""),
                "needs_update": r.get("needs_update", "uncertain"),
                "confidence": r.get("confidence", "unknown"),
                "summary": r.get("summary", ""),
            })

    # Saved report JSON files
    seen_ids = {r["job_id"] for r in results}
    for path in sorted(glob.glob("data/report_*.json"), reverse=True):
        try:
            job_id = Path(path).stem.replace("report_", "")
            if job_id in seen_ids:
                continue
            with open(path) as f:
                data = json.load(f)
            # Handle both list and dict formats
            report = data[0] if isinstance(data, list) else data
            results.append({
                "job_id": job_id,
                "doe_directive_id": report.get("doe_directive_id", ""),
                "netl_directive_id": report.get("netl_directive_id", ""),
                "needs_update": report.get("needs_update", "uncertain"),
                "confidence": report.get("confidence", "unknown"),
                "summary": report.get("summary", ""),
            })
        except Exception:
            continue

    return JSONResponse(results)


# Serve frontend — SPA catch-all: serve index.html for all non-API routes
app.mount("/", StaticFiles(directory="static", html=True), name="static")
