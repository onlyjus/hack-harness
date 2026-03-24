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
from fastapi import FastAPI, File, UploadFile, HTTPException
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

        job["report"] = result
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


# Serve frontend
app.mount("/", StaticFiles(directory="static", html=True), name="static")
