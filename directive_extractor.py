"""LLM-based directive extraction and deep comparison using Semantic Kernel.

Uses a multi-pass approach:
1. Extract structured metadata from each PDF (ID, title, dates).
2. Extract detailed requirements, responsibilities, and sections from each PDF
   in chunks to handle large documents.
3. Perform a deep section-by-section comparison of DOE vs NETL orders.
"""

import json
from dataclasses import dataclass, asdict
from pathlib import Path

from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion, AzureChatPromptExecutionSettings
from semantic_kernel.contents.chat_history import ChatHistory


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """\
You are a document analysis assistant. Given text extracted from a government \
directive or procedure PDF, extract the following fields. Respond ONLY with a \
JSON object — no markdown fences, no explanation.

{
  "directive_id": "<identifier such as DOE O 151.1D, or N/A if not found>",
  "title": "<full title of the directive>",
  "publication_year": <four-digit year as integer, or null if not found>,
  "effective_date": "<effective/issue date string as written, or null>",
  "summary": "<2-3 sentence summary of the directive's purpose>"
}

If a field cannot be determined from the text, use null for dates/years and \
"N/A" for strings.
"""


SECTION_EXTRACTION_PROMPT = """\
You are a document analysis assistant specializing in government directives. \
Given a chunk of text from a directive or order PDF, extract ALL substantive \
content you can find. Respond ONLY with a JSON object — no markdown fences, \
no explanation.

Extract the following from this chunk of text:

{
  "sections": [
    {
      "section_id": "<section number/letter if present, e.g. '3.a', 'Chapter II', or 'unnumbered'>",
      "heading": "<section heading or topic>",
      "content_summary": "<detailed summary of what this section requires or states>"
    }
  ],
  "requirements": [
    {
      "requirement_id": "<requirement number or reference if present>",
      "description": "<the specific requirement, obligation, or mandate>",
      "responsible_party": "<who is responsible, if stated>",
      "timeframe": "<any deadlines or frequencies mentioned, or null>"
    }
  ],
  "definitions": [
    {
      "term": "<defined term>",
      "definition": "<the definition>"
    }
  ],
  "references": [
    "<any directive, order, law, or regulation referenced (e.g. 'DOE O 151.1D', '10 CFR 851')>"
  ],
  "roles_and_responsibilities": [
    {
      "role": "<role or position title>",
      "responsibilities": "<what this role must do>"
    }
  ]
}

Include everything you find. If a category has no items in this chunk, use an \
empty list []. Be thorough — capture specific numeric thresholds, deadlines, \
frequencies, organizational names, and procedural details.
"""


COMPARISON_PROMPT = """\
You are an expert analyst specializing in DOE (Department of Energy) and NETL \
(National Energy Technology Laboratory) directives, orders, and procedures.

You are given the extracted detailed content from two documents:
1. A DOE order (the parent/governing directive)
2. An NETL order (the site-level implementation of the DOE order)

Perform a THOROUGH section-by-section comparison. Go through every requirement, \
responsibility, definition, and reference. Identify ALL differences, not just \
high-level ones.

Analyze:
1. VERSION ALIGNMENT: Does the NETL order reference the current version of the DOE order?
2. REQUIREMENTS COVERAGE: For each DOE requirement, is it addressed in the NETL order? \
   Are there DOE requirements the NETL order is missing entirely?
3. RESPONSIBILITIES: Do the roles and responsibilities in the NETL order match what the \
   DOE order requires? Are there new roles in the DOE order not reflected in NETL?
4. DEFINITIONS: Are the NETL definitions consistent with DOE definitions? Are any missing?
5. REFERENCES: Does the NETL order reference current versions of cited directives and laws?
6. THRESHOLDS & DEADLINES: Do numeric thresholds, frequencies, and deadlines match?
7. SCOPE: Does the NETL order cover the full scope of the DOE order, or are areas missing?
8. REMOVED/CHANGED CONTENT: Are there items in the NETL order that have been removed or \
   changed in the newer DOE order?

Respond ONLY with a JSON object — no markdown fences, no explanation:

{
  "needs_update": "<yes, no, or uncertain>",
  "confidence": "<high, medium, or low>",
  "doe_directive_id": "<DOE order identifier>",
  "netl_directive_id": "<NETL order identifier>",
  "doe_effective_date": "<DOE order effective date or null>",
  "netl_effective_date": "<NETL order effective date or null>",
  "version_alignment": {
    "aligned": <true or false>,
    "detail": "<explanation of version alignment status>"
  },
  "summary": "<3-5 sentence overall summary of the comparison>",
  "section_by_section": [
    {
      "topic": "<section topic or area>",
      "doe_content": "<what the DOE order says>",
      "netl_content": "<what the NETL order says, or 'NOT ADDRESSED'>",
      "status": "<aligned, misaligned, missing_from_netl, outdated, or netl_only>",
      "detail": "<specific explanation of the difference>"
    }
  ],
  "missing_requirements": [
    {
      "doe_requirement": "<requirement from DOE order>",
      "detail": "<why this is missing or inadequate in NETL order>"
    }
  ],
  "outdated_references": [
    {
      "netl_reference": "<what the NETL order cites>",
      "current_reference": "<what it should cite based on DOE order>",
      "detail": "<explanation>"
    }
  ],
  "responsibility_gaps": [
    {
      "role": "<role or position>",
      "gap": "<what is missing or different>"
    }
  ],
  "definition_differences": [
    {
      "term": "<term>",
      "doe_definition": "<DOE definition>",
      "netl_definition": "<NETL definition or 'MISSING'>",
      "detail": "<explanation of the difference>"
    }
  ],
  "recommendations": [
    {
      "priority": "<high, medium, or low>",
      "section": "<affected section or area>",
      "action": "<specific recommended change to the NETL order>"
    }
  ]
}
"""

CHUNK_COMPARISON_PROMPT = """\
You are continuing a detailed comparison of a DOE order vs an NETL order. \
You have already seen earlier portions of these documents. Now review this \
additional chunk and identify any NEW findings not already covered.

Focus on specific requirements, responsibilities, deadlines, definitions, \
and references in this chunk. Respond ONLY with a JSON object:

{
  "additional_section_findings": [
    {
      "topic": "<section topic or area>",
      "doe_content": "<what the DOE order says in this chunk>",
      "netl_content": "<what the NETL order says, or 'NOT ADDRESSED'>",
      "status": "<aligned, misaligned, missing_from_netl, outdated, or netl_only>",
      "detail": "<specific explanation>"
    }
  ],
  "additional_missing_requirements": [
    {
      "doe_requirement": "<requirement from DOE order>",
      "detail": "<why missing or inadequate in NETL>"
    }
  ],
  "additional_recommendations": [
    {
      "priority": "<high, medium, or low>",
      "section": "<affected section>",
      "action": "<specific recommended change>"
    }
  ]
}

If this chunk has no new findings beyond what was already covered, return \
empty lists for all fields.
"""


@dataclass
class DirectiveInfo:
    """Structured metadata extracted from a directive PDF."""

    source_file: str
    directive_id: str
    title: str
    publication_year: int | None
    effective_date: str | None
    summary: str

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_code_fences(raw: str) -> str:
    """Remove markdown code fences from an LLM response."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


def _parse_json_response(raw: str, context: str) -> dict:
    """Parse JSON from an LLM response, stripping fences if needed."""
    cleaned = _strip_code_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to parse LLM response as JSON ({context}): {exc}\n"
            f"Raw response: {cleaned[:500]}"
        ) from exc


def _chunk_text(text: str, chunk_size: int, overlap: int = 500) -> list[str]:
    """Split text into overlapping chunks for processing.

    Args:
        text: Full document text.
        chunk_size: Target size for each chunk in characters.
        overlap: Characters of overlap between chunks for context continuity.

    Returns:
        List of text chunks.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start = end - overlap
    return chunks


# ---------------------------------------------------------------------------
# Extraction functions
# ---------------------------------------------------------------------------


async def extract_directive_info(
    chat_service: AzureChatCompletion,
    pdf_text: str,
    source_filename: str,
    max_text_chars: int = 12000,
) -> DirectiveInfo:
    """Send PDF text to the LLM and parse directive metadata."""
    truncated = pdf_text[:max_text_chars]

    history = ChatHistory()
    history.add_system_message(EXTRACTION_PROMPT)
    history.add_user_message(
        f"Extract directive information from this document "
        f"(filename: {source_filename}):\n\n{truncated}"
    )

    settings = AzureChatPromptExecutionSettings(temperature=0.1)
    response = await chat_service.get_chat_message_content(
        chat_history=history, settings=settings,
    )

    parsed = _parse_json_response(str(response), f"metadata for {source_filename}")

    return DirectiveInfo(
        source_file=source_filename,
        directive_id=str(parsed.get("directive_id", "N/A")),
        title=str(parsed.get("title", "N/A")),
        publication_year=parsed.get("publication_year"),
        effective_date=parsed.get("effective_date"),
        summary=str(parsed.get("summary", "N/A")),
    )


async def extract_directive_sections(
    chat_service: AzureChatCompletion,
    full_text: str,
    source_filename: str,
    chunk_size: int = 12000,
) -> dict:
    """Extract detailed sections, requirements, and roles from a full document.

    Processes the document in chunks and merges results.

    Returns:
        Merged dict with sections, requirements, definitions, references,
        and roles_and_responsibilities.
    """
    chunks = _chunk_text(full_text, chunk_size)
    merged: dict = {
        "sections": [],
        "requirements": [],
        "definitions": [],
        "references": [],
        "roles_and_responsibilities": [],
    }

    settings = AzureChatPromptExecutionSettings(temperature=0.1)

    for i, chunk in enumerate(chunks):
        history = ChatHistory()
        history.add_system_message(SECTION_EXTRACTION_PROMPT)
        history.add_user_message(
            f"Extract content from chunk {i + 1}/{len(chunks)} of "
            f"{source_filename}:\n\n{chunk}"
        )

        response = await chat_service.get_chat_message_content(
            chat_history=history, settings=settings,
        )

        parsed = _parse_json_response(
            str(response), f"section extraction chunk {i + 1} of {source_filename}"
        )

        for key in merged:
            items = parsed.get(key, [])
            if isinstance(items, list):
                merged[key].extend(items)

    # Deduplicate references
    merged["references"] = list(dict.fromkeys(merged["references"]))

    return merged


async def compare_directives(
    chat_service: AzureChatCompletion,
    doe_info: DirectiveInfo,
    doe_text: str,
    netl_info: DirectiveInfo,
    netl_text: str,
    doe_sections: dict | None = None,
    netl_sections: dict | None = None,
    chunk_size: int = 40000,
) -> dict:
    """Deep comparison of a DOE order vs an NETL order.

    If pre-extracted sections are provided, they are included for richer
    context. The full document text is also sent in chunks so the LLM can
    crawl through the details.

    Uses a multi-pass approach:
    1. First pass: send metadata + extracted sections + first chunk of each doc.
    2. Additional passes: send remaining chunks for incremental findings.
    3. Merge all findings into a single report.
    """
    settings = AzureChatPromptExecutionSettings(temperature=0.1)

    # Build context block from extracted sections (if available)
    doe_sections_text = ""
    netl_sections_text = ""
    if doe_sections:
        doe_sections_text = (
            f"\n\n--- DOE Extracted Structure ---\n"
            f"Sections: {json.dumps(doe_sections.get('sections', []), indent=1)}\n"
            f"Requirements: {json.dumps(doe_sections.get('requirements', []), indent=1)}\n"
            f"Definitions: {json.dumps(doe_sections.get('definitions', []), indent=1)}\n"
            f"References: {json.dumps(doe_sections.get('references', []))}\n"
            f"Roles: {json.dumps(doe_sections.get('roles_and_responsibilities', []), indent=1)}"
        )
    if netl_sections:
        netl_sections_text = (
            f"\n\n--- NETL Extracted Structure ---\n"
            f"Sections: {json.dumps(netl_sections.get('sections', []), indent=1)}\n"
            f"Requirements: {json.dumps(netl_sections.get('requirements', []), indent=1)}\n"
            f"Definitions: {json.dumps(netl_sections.get('definitions', []), indent=1)}\n"
            f"References: {json.dumps(netl_sections.get('references', []))}\n"
            f"Roles: {json.dumps(netl_sections.get('roles_and_responsibilities', []), indent=1)}"
        )

    # Chunk both documents
    doe_chunks = _chunk_text(doe_text, chunk_size)
    netl_chunks = _chunk_text(netl_text, chunk_size)

    # --- Pass 1: Primary comparison with metadata + sections + first chunks ---
    doe_first = doe_chunks[0] if doe_chunks else ""
    netl_first = netl_chunks[0] if netl_chunks else ""

    primary_message = (
        f"Compare the following two directives in detail:\n\n"
        f"=== DOE ORDER ===\n"
        f"Directive ID: {doe_info.directive_id}\n"
        f"Title: {doe_info.title}\n"
        f"Effective Date: {doe_info.effective_date}\n"
        f"Publication Year: {doe_info.publication_year}\n"
        f"Summary: {doe_info.summary}\n"
        f"{doe_sections_text}\n\n"
        f"--- DOE Document Text (Part 1/{len(doe_chunks)}) ---\n"
        f"{doe_first}\n\n"
        f"=== NETL ORDER ===\n"
        f"Directive ID: {netl_info.directive_id}\n"
        f"Title: {netl_info.title}\n"
        f"Effective Date: {netl_info.effective_date}\n"
        f"Publication Year: {netl_info.publication_year}\n"
        f"Summary: {netl_info.summary}\n"
        f"{netl_sections_text}\n\n"
        f"--- NETL Document Text (Part 1/{len(netl_chunks)}) ---\n"
        f"{netl_first}"
    )

    history = ChatHistory()
    history.add_system_message(COMPARISON_PROMPT)
    history.add_user_message(primary_message)

    response = await chat_service.get_chat_message_content(
        chat_history=history, settings=settings,
    )
    result = _parse_json_response(str(response), "primary comparison")

    # --- Additional passes for remaining chunks ---
    remaining_chunks: list[tuple[str, str, int]] = []
    for i, chunk in enumerate(doe_chunks[1:], 2):
        remaining_chunks.append(("DOE", chunk, i))
    for i, chunk in enumerate(netl_chunks[1:], 2):
        remaining_chunks.append(("NETL", chunk, i))

    for label, chunk, chunk_num in remaining_chunks:
        total = len(doe_chunks) if label == "DOE" else len(netl_chunks)
        follow_history = ChatHistory()
        follow_history.add_system_message(CHUNK_COMPARISON_PROMPT)
        follow_history.add_user_message(
            f"Previous comparison found these key findings:\n"
            f"Needs update: {result.get('needs_update')}\n"
            f"Summary so far: {result.get('summary', '')}\n\n"
            f"Now review this additional {label} document text "
            f"(Part {chunk_num}/{total}):\n\n{chunk}"
        )

        resp = await chat_service.get_chat_message_content(
            chat_history=follow_history, settings=settings,
        )

        try:
            additional = _parse_json_response(str(resp), f"chunk {label} {chunk_num}")
        except RuntimeError:
            continue

        # Merge additional findings
        for finding in additional.get("additional_section_findings", []):
            result.setdefault("section_by_section", []).append(finding)
        for req in additional.get("additional_missing_requirements", []):
            result.setdefault("missing_requirements", []).append(req)
        for rec in additional.get("additional_recommendations", []):
            result.setdefault("recommendations", []).append(rec)

    # Attach source file info for traceability
    result["doe_source_file"] = doe_info.source_file
    result["netl_source_file"] = netl_info.source_file

    return result


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_catalog(directives: list[DirectiveInfo], output_path: str | Path) -> Path:
    """Save extracted directive metadata to a JSON file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [d.to_dict() for d in directives]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def load_catalog(catalog_path: str | Path) -> list[DirectiveInfo]:
    """Load a previously saved directive catalog from JSON."""
    path = Path(catalog_path)
    if not path.exists():
        return []

    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        DirectiveInfo(
            source_file=item.get("source_file", ""),
            directive_id=item.get("directive_id", "N/A"),
            title=item.get("title", "N/A"),
            publication_year=item.get("publication_year"),
            effective_date=item.get("effective_date"),
            summary=item.get("summary", "N/A"),
        )
        for item in data
    ]


def save_comparison_report(comparisons: list[dict], output_path: str | Path) -> Path:
    """Save comparison results to a JSON file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(comparisons, indent=2), encoding="utf-8")
    return path
