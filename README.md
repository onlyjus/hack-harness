# hack-harness

A Python tool for comparing DOE (Department of Energy) orders against NETL (National Energy Technology Laboratory) orders to determine if the NETL orders need updating.

## What this does

- Accepts locally provided DOE and NETL order PDFs (no SharePoint API required).
- Extracts text and structured metadata from each PDF using PyMuPDF.
- Uses an LLM via Semantic Kernel to compare the orders and identify gaps.
- Produces a JSON comparison report indicating whether NETL orders need updating.
- Includes a **web UI** for uploading PDFs, tracking progress, and viewing results.
- Includes an interactive chat CLI for asking questions about directives.

## Files

- `app.py` - FastAPI web server (upload, progress streaming, results)
- `static/index.html` - browser-based frontend
- `process_directives.py` - batch comparison pipeline (DOE vs NETL)
- `directive_extractor.py` - LLM-based metadata extraction and comparison
- `pdf_extractor.py` - PDF text extraction using PyMuPDF
- `chat_cli.py` - interactive CLI chat app
- `agents/directives.yaml` - system prompt for directive comparison
- `requirements.txt` - Python dependencies
- `.env` - runtime configuration

## Setup

1. Create and activate a virtual environment (optional but recommended).
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in your Azure OpenAI or Foundry credentials.

## Web UI

Start the web server:

```bash
uvicorn app:app --reload --port 3000
```

Open `http://localhost:3000` in a browser. The UI provides three steps:

1. **Upload** — drag-and-drop (or browse) a DOE order PDF and an NETL order PDF.
2. **Progress** — a live progress bar and log stream showing each pipeline stage (text extraction → metadata → section extraction → comparison).
3. **Results** — a formatted report showing:
   - Update verdict (update needed / up to date / uncertain) with confidence level
   - Version alignment status
   - Section-by-section findings with status badges
   - Missing requirements
   - Outdated references
   - Responsibility gaps
   - Definition differences
   - Prioritized recommendations

## CLI Batch Pipeline

For processing multiple files or scripted workflows, use the CLI directly:

```bash
# Compare directories of PDFs
python process_directives.py --doe data/doe/ --netl data/netl/

# Compare specific files
python process_directives.py --doe path/to/doe_order.pdf --netl path/to/netl_order.pdf

# Custom output path
python process_directives.py --doe data/doe/ --netl data/netl/ -o my_report.json
```

The pipeline will:
1. Extract text from all provided PDFs.
2. Use the LLM to identify directive metadata (ID, title, dates, summary).
3. Extract detailed sections, requirements, definitions, roles, and references.
4. Compare each NETL order against the DOE order(s).
5. Output a JSON report with findings, gaps, and update recommendations.

### Output Format

The comparison report (`data/comparison_report.json`) contains entries like:

```json
{
  "needs_update": "yes",
  "confidence": "high",
  "doe_directive_id": "DOE O 151.1D",
  "netl_directive_id": "NETL O 151.1-1",
  "summary": "The NETL order references an older version...",
  "section_by_section": [...],
  "missing_requirements": [...],
  "outdated_references": [...],
  "responsibility_gaps": [...],
  "definition_differences": [...],
  "recommendations": [...]
}
```

## Interactive Chat

You can also use the interactive chat CLI to discuss directives:

```bash
AGENT_PROMPT_FILE=agents/directives.yaml python chat_cli.py
```

Type your message at `you>`. The app exits on `Ctrl+C` or `Ctrl+X`.

## LLM Configuration

### Option A: Azure OpenAI

```env
CHAT_PROVIDER=azure_openai

AZURE_OPENAI_ENDPOINT=https://<your-openai-resource>.openai.azure.com
AZURE_OPENAI_API_KEY=<your-api-key-or-leave-blank-for-default-credential>
AZURE_OPENAI_CHAT_DEPLOYMENT=<your-chat-deployment-name>
AZURE_OPENAI_API_VERSION=2024-10-21
```

### Option B: Foundry project endpoint

```env
CHAT_PROVIDER=foundry

FOUNDRY_PROJECT_ENDPOINT=https://<your-project-host>.services.ai.azure.com/api/projects/<your-project-name>
FOUNDRY_API_KEY=<your-api-key-or-leave-blank-for-default-credential>
FOUNDRY_CHAT_DEPLOYMENT=<your-chat-deployment-name>
FOUNDRY_API_VERSION=2024-10-21
```

## Notes

- If the API key value is blank, the app falls back to Azure Default Credential.
- The comparison pipeline sends document text to the LLM in chunks, so large documents are fully processed across multiple passes.
- Scanned/image-only PDFs will not yield text — ensure PDFs contain selectable text.
