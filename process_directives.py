"""Compare DOE and NETL directive PDFs to determine if NETL orders need updating.

Usage:
    python process_directives.py --doe data/doe/ --netl data/netl/
    python process_directives.py --doe doe_order.pdf --netl netl_order.pdf
    python process_directives.py --doe data/doe/ --netl data/netl/ -o report.json

Place DOE order PDFs in one directory and corresponding NETL order PDFs in
another, then run this script. It will:

1. Extract text from each PDF.
2. Use the LLM to extract structured directive metadata.
3. Compare each NETL order against relevant DOE orders.
4. Produce a comparison report indicating whether NETL orders need updating.

Requires Foundry or Azure OpenAI credentials configured in .env.
"""

import argparse
import asyncio
import glob
import sys
from pathlib import Path

from dotenv import load_dotenv

from pdf_extractor import extract_text, extract_first_n_pages
from directive_extractor import (
    extract_directive_info,
    extract_directive_sections,
    compare_directives,
    save_catalog,
    save_comparison_report,
    DirectiveInfo,
)


def _collect_pdfs(path: str) -> list[Path]:
    """Collect PDF files from a path (file or directory)."""
    p = Path(path)
    if p.is_file() and p.suffix.lower() == ".pdf":
        return [p]
    if p.is_dir():
        pdfs = sorted(p.glob("*.pdf"))
        if not pdfs:
            print(f"  WARNING: No PDF files found in {p}")
        return pdfs
    print(f"  WARNING: {p} is not a valid PDF file or directory")
    return []


def _build_chat_service():
    """Build the Semantic Kernel chat service from env config."""
    from chat_cli import load_config, create_chat_service
    config = load_config()
    return create_chat_service(config)


async def _extract_directives(
    pdf_paths: list[Path],
    label: str,
    chat_service,
) -> list[tuple[DirectiveInfo, str, dict]]:
    """Extract metadata, full text, and detailed sections from a list of PDFs.

    Returns list of (DirectiveInfo, full_text, sections_dict) tuples.
    """
    results: list[tuple[DirectiveInfo, str, dict]] = []
    for i, pdf_path in enumerate(pdf_paths, 1):
        print(f"  [{i}/{len(pdf_paths)}] {pdf_path.name}...")

        # Extract text
        full_text = extract_text(pdf_path)
        if not full_text.strip():
            print(f"    WARNING: No text extracted (may be scanned image)")
            continue

        first_pages = extract_first_n_pages(pdf_path, n=5)
        print(f"    {len(full_text):,} chars extracted")

        # LLM metadata extraction
        try:
            info = await extract_directive_info(chat_service, first_pages, pdf_path.name)
            print(f"    → {info.directive_id} | {info.title[:60]}")
        except Exception as exc:
            print(f"    → [ERROR] metadata extraction: {exc}")
            continue

        # Deep section extraction (processes entire document in chunks)
        print(f"    Extracting detailed sections...")
        try:
            sections = await extract_directive_sections(
                chat_service, full_text, pdf_path.name,
            )
            n_secs = len(sections.get("sections", []))
            n_reqs = len(sections.get("requirements", []))
            n_roles = len(sections.get("roles_and_responsibilities", []))
            n_defs = len(sections.get("definitions", []))
            n_refs = len(sections.get("references", []))
            print(f"    → {n_secs} sections, {n_reqs} requirements, "
                  f"{n_roles} roles, {n_defs} definitions, {n_refs} references")
        except Exception as exc:
            print(f"    → [WARNING] section extraction failed: {exc}")
            sections = {}

        results.append((info, full_text, sections))

    return results


async def process_all(doe_path: str, netl_path: str, output_path: str) -> None:
    """Run the full comparison pipeline."""

    load_dotenv(override=False)

    print("=== DOE / NETL Directive Comparison Pipeline ===\n")

    # Collect PDFs
    doe_pdfs = _collect_pdfs(doe_path)
    netl_pdfs = _collect_pdfs(netl_path)

    print(f"DOE PDFs:  {len(doe_pdfs)} file(s) from {doe_path}")
    print(f"NETL PDFs: {len(netl_pdfs)} file(s) from {netl_path}")

    if not doe_pdfs or not netl_pdfs:
        print("\nNeed at least one DOE PDF and one NETL PDF to compare. Exiting.")
        return

    # Build LLM service
    print("\n[1/3] Initializing LLM service...")
    chat_service = _build_chat_service()

    # Extract directive info from DOE orders
    print("\n[2/3] Extracting directive information...")
    print(f"\n  --- DOE Orders ---")
    doe_directives = await _extract_directives(doe_pdfs, "DOE", chat_service)

    print(f"\n  --- NETL Orders ---")
    netl_directives = await _extract_directives(netl_pdfs, "NETL", chat_service)

    if not doe_directives or not netl_directives:
        print("\nFailed to extract directives from one or both sets. Exiting.")
        return

    # Compare each NETL order against the DOE orders
    print(f"\n[3/3] Deep comparison of {len(netl_directives)} NETL order(s) against "
          f"{len(doe_directives)} DOE order(s)...")
    print(f"  (analyzing sections, requirements, definitions, roles, references)")

    comparisons: list[dict] = []
    for netl_info, netl_text, netl_sections in netl_directives:
        for doe_info, doe_text, doe_sections in doe_directives:
            print(f"\n  Comparing: {netl_info.directive_id} (NETL) ↔ {doe_info.directive_id} (DOE)")
            try:
                result = await compare_directives(
                    chat_service,
                    doe_info=doe_info,
                    doe_text=doe_text,
                    netl_info=netl_info,
                    netl_text=netl_text,
                    doe_sections=doe_sections,
                    netl_sections=netl_sections,
                )
                comparisons.append(result)
                needs_update = result.get("needs_update", "unknown")
                confidence = result.get("confidence", "unknown")
                print(f"    → Needs update: {needs_update} (confidence: {confidence})")

                # Print detailed findings summary
                n_section_findings = len(result.get("section_by_section", []))
                n_missing = len(result.get("missing_requirements", []))
                n_outdated_refs = len(result.get("outdated_references", []))
                n_resp_gaps = len(result.get("responsibility_gaps", []))
                n_def_diffs = len(result.get("definition_differences", []))
                n_recs = len(result.get("recommendations", []))
                print(f"    → {n_section_findings} section findings, "
                      f"{n_missing} missing requirements, "
                      f"{n_outdated_refs} outdated references")
                print(f"    → {n_resp_gaps} responsibility gaps, "
                      f"{n_def_diffs} definition differences, "
                      f"{n_recs} recommendations")

                if result.get("summary"):
                    print(f"    → {result['summary'][:200]}")
            except Exception as exc:
                print(f"    → [ERROR] {exc}")

    # Save results
    if comparisons:
        report_path = save_comparison_report(comparisons, output_path)
        print(f"\n=== Done! Comparison report saved to {report_path} ===")
        print(f"  Total comparisons: {len(comparisons)}")

        # Summary
        updates_needed = sum(1 for c in comparisons if c.get("needs_update") == "yes")
        up_to_date = sum(1 for c in comparisons if c.get("needs_update") == "no")
        uncertain = len(comparisons) - updates_needed - up_to_date
        print(f"  Updates needed: {updates_needed}")
        print(f"  Up to date: {up_to_date}")
        if uncertain:
            print(f"  Uncertain: {uncertain}")
    else:
        print("\n=== No comparisons were completed. ===")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare DOE and NETL directive PDFs to determine if NETL orders need updating.",
    )
    parser.add_argument(
        "--doe",
        required=True,
        help="Path to DOE order PDF file or directory containing DOE PDFs",
    )
    parser.add_argument(
        "--netl",
        required=True,
        help="Path to NETL order PDF file or directory containing NETL PDFs",
    )
    parser.add_argument(
        "--output", "-o",
        default="data/comparison_report.json",
        help="Output path for the comparison report JSON (default: data/comparison_report.json)",
    )
    args = parser.parse_args()
    asyncio.run(process_all(args.doe, args.netl, args.output))


if __name__ == "__main__":
    main()
