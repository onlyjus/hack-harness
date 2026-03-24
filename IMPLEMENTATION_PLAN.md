# Implementation Plan: Architectural Archive UI

## Overview

Transform the current single-page dark-themed upload tool (`static/index.html`) into the multi-view "Architectural Archive" experience defined in `sketch/DESIGN.md` and the four sketch mockups. The backend (`app.py`) needs new endpoints to support the richer UI, and the frontend becomes a single-page application with hash routing and shared layout components.

---

## Phase 1: Foundation — Design System & Shared Layout ✅ COMPLETED

**Goal:** Establish the design token layer, shared components (sidebar + top bar), and SPA routing so all subsequent work builds on a consistent base.

### 1.1 Design Tokens & Tailwind Config

- Create a shared Tailwind config (inline `<script>`) matching `sketch/DESIGN.md`:
  - **Surface hierarchy:** `surface` (#fff8f8), `surface-container-low` (#fbf1f2), `surface-container` (#f5eced), `surface-container-highest` (#e9e0e1), `surface-container-lowest` (#ffffff)
  - **Colors:** Primary (#00193c), secondary (#466800 energy green), tertiary (#001b35), error (#ba1a1a), on-primary (#ffffff), on-secondary-container (#4b6f00), outline-variant (#c4c6d1)
  - **Typography:** Public Sans with display/headline/title/body/label scale, tight letter-spacing for headlines (-0.02em), generous body line-height (1.6), all-caps label tracking (+0.05em)
  - **Spacing tokens:** spacing-4 (1rem) through spacing-16 (4rem)
  - **Ghost border utility:** `outline-variant` (#c4c6d1) at 15% opacity
  - **Ambient shadow presets:** 24–40px blur, 4–6% opacity, tinted `on-surface` (#1e1b1c)
  - **Glassmorphism utility:** 80% opacity surface + `backdrop-blur: 12px`

### 1.2 Shared Layout Shell

Build a layout template within a single `index.html` containing:

- **SideNavBar** (fixed left, 256px, `surface-container-low` bg):
  - Logo/branding block (NETL icon + "Document Archive" subtitle)
  - Nav links: Dashboard, Directives Library, Comparison Tool, Gap Analysis, History
  - Bottom actions: New Comparison button, Help link, Admin link
  - Active nav state: `bg-[#e9e0e1]` with `border-r-4 border-[#00193c]`, bold text, slight `scale-[0.98]`
- **TopAppBar** (sticky top, `surface` bg):
  - App title ("Architectural Archive")
  - Search input (rounded, ghost-bordered)
  - Tab nav: Directives / DOE Orders / Manuals
  - Notification + settings icon buttons + user avatar
- **Content slot** — `ml-64 flex-1` main area for page-specific content

### 1.3 Client-Side Routing (Option B — Lightweight SPA)

Single `index.html` with hash routing:

- Routes: `#/dashboard`, `#/library`, `#/compare`, `#/compare/{job_id}`, `#/gaps/{job_id}`, `#/history`
- A minimal JS router that:
  - Listens to `hashchange` events
  - Matches route patterns and extracts parameters (e.g., `job_id`)
  - Calls the appropriate render function for each view
  - Updates the active nav state in the sidebar
- Page content rendered via JS into a `<main id="app">` container
- Shared state object for selected directives, active job, etc.
- Default route: `#/dashboard`

### 1.4 Backend: SPA Catch-All Route

- Update `app.py` to serve `index.html` for all non-API routes so that direct navigation to `#/compare/abc123` works after a page refresh.
- Keep the existing static file mount but add a fallback.

### Files Modified/Created

| Action | File | Purpose |
|--------|------|---------|
| Rewrite | `static/index.html` | SPA shell with design system, router, shared layout |
| Modify | `app.py` | SPA catch-all route for non-API paths |

---

## Phase 2: Dashboard View

**Goal:** Implement the overview dashboard from `sketch/dashboard.html`.

### 2.1 Backend — Dashboard Data Endpoint

- `GET /api/dashboard` — Returns:
  ```json
  {
    "pending_comparisons": 24,
    "recent_gaps_found": 7,
    "in_progress_edits": 15,
    "total_validated": 142,
    "recent_documents": [
      {
        "id": "...",
        "title": "NETL M 450.4-1A",
        "type": "netl",
        "status": "active",
        "last_modified": "...",
        "tags": []
      }
    ],
    "review_velocity": {
      "current_month": 32,
      "change_pct": 14
    }
  }
  ```
- Source data from existing `data/` directory reports and in-memory `jobs` store.

### 2.2 Frontend — Dashboard Components

- **Summary Bento Grid:** 4 metric cards in a 4-column grid:
  - Pending Comparisons (count + trend %)
  - Recent Gaps Found (count + severity indicator)
  - In-Progress Edits (count)
  - Total Validated (count)
  - Each card: `surface-container-highest` bg, rounded-xl, centered layout, label-md metadata, display-sized number
- **Recent Documents Feed** (7/12 width):
  - Document rows with status badges (`Active` = green, `Gap Detected` = red, `Review Pending` = yellow)
  - Directive type chips (NETL = primary, DOE = tertiary)
  - Timestamps and action buttons
- **Activity & Analytics Sidebar** (5/12 width):
  - Review Velocity bar chart (CSS-only or lightweight chart lib)
  - Featured Collection hero panel with gradient background
- **Floating Action Toolbar:**
  - Bottom-right positioned, glassmorphism styling (80% opacity + 12px blur)
  - Buttons: "+", "PDF" upload shortcut, "COMPARE" CTA in secondary green

### Files Modified

| Action | File | Purpose |
|--------|------|---------|
| Add | `static/index.html` | Dashboard view render function |
| Add | `app.py` | `GET /api/dashboard` endpoint |

---

## Phase 3: Directives Library View

**Goal:** Implement the browsable library from `sketch/library.html`.

### 3.1 Backend — Library Endpoints

- `GET /api/directives` — List all known directives with filtering:
  - Query params: `?department=&source_type=doe|netl|all&status=current|draft|archived&sort=updated`
  - Returns: Array of directive summary objects (`id`, `title`, `type`, `status`, `last_modified`, `version`, `summary`)
- `GET /api/directives/{id}` — Full directive detail (metadata + extracted sections)
- Source data from saved catalog JSONs in `data/`, plus metadata from completed comparison jobs.

### 3.2 Frontend — Library Components

- **Header:** Title ("Directives Library"), description, action buttons (Advanced Filters, Compare Selected)
- **Filter Bar** (4-column grid):
  - Department dropdown
  - Source Type toggle (All / DOE / NETL)
  - Status dropdown (Current Only / Draft / Archived)
  - Last Updated dropdown
- **Bento Grid Layout:**
  - Large featured card (8-col span): Most recently updated directive with full metadata and "View Document" CTA
  - Secondary card (4-col): "Select to Compare" action
  - Standard grid items (4-col each): Icon, badge, title, summary, last modified date
- **Asymmetric Utility Section** (3 + 9 col):
  - Left: Archive Stats sidebar (total docs, updated count, digitized %)
  - Right: Dark hero panel with "Intelligent Comparison Engine" CTA
- **Selection State:** Track selected directives; "Compare Selected (N)" button activates when 2+ selected, navigates to `#/compare` with selected IDs

### Files Modified

| Action | File | Purpose |
|--------|------|---------|
| Add | `static/index.html` | Library view render function |
| Add | `app.py` | `GET /api/directives`, `GET /api/directives/{id}` endpoints |

---

## Phase 4: Comparison Tool View (Core Feature) ✅ COMPLETED

**Goal:** Implement the side-by-side comparison workspace from `sketch/comparison_tool.html`. This is the most complex view and the primary value of the application.

### 4.1 Backend — Enhanced Comparison API

**Keep existing endpoints:**
- `POST /api/upload` — Accept DOE + NETL PDFs, start comparison job
- `GET /api/status/{job_id}` — SSE progress stream
- `GET /api/report/{job_id}` — Final comparison report

**New endpoints:**
- `GET /api/report/{job_id}/sections` — Return DOE and NETL extracted sections side-by-side with cross-mapping:
  ```json
  {
    "doe_sections": [
      {
        "id": "1.0",
        "heading": "Scope",
        "content": "...",
        "mapped_netl_section": "Section I",
        "findings": []
      }
    ],
    "netl_sections": [
      {
        "id": "I",
        "heading": "Purpose",
        "content": "...",
        "mapped_doe_section": "1.0",
        "findings": []
      }
    ],
    "unmapped_doe_sections": ["3.0"],
    "unmapped_netl_sections": []
  }
  ```
- `GET /api/report/{job_id}/gaps` — Return gap analysis summary (discrepancy count, critical gap count, issue cards)
- `POST /api/report/{job_id}/suggest` — Trigger AI-generated edit suggestion for a specific gap:
  - Input: `{ "gap_id": "...", "context": "..." }`
  - Output: `{ "suggested_text": "...", "rationale": "..." }`

**Enhance `_run_pipeline`:**
- Persist extracted sections (DOE + NETL) alongside the comparison report in the job's JSON file
- Add section-level cross-references to the report schema

### 4.2 Frontend — Upload Flow (Restyled)

- Preserve existing upload logic (FormData POST, SSE progress listener)
- Restyle to match design system:
  - Drop zones with `surface-container` bg, ghost borders, Public Sans typography
  - Progress bar using primary gradient
  - Log entries with tonal stage tags
- After upload completes → auto-navigate to `#/compare/{job_id}`

### 4.3 Frontend — Comparison Workspace (3-Panel Layout)

**Left Panel — DOE Order Viewer (`flex-1`, `surface` bg, right border via tonal shift):**
- Header: "DOE Source" badge (tertiary-container chip), version tag, directive title + description
- Scrollable sections rendered from `doe_sections` data
- Discrepancy indicators: numbered red circles (`bg-error`) positioned on sections with findings
- Section highlighting: `surface-container-highest` bg + `border-l-4 border-error/40` for problem sections
- Normal sections: `surface-container-low/50` bg, rounded-xl

**Middle Panel — NETL Directive Viewer (`flex-1`, `surface-container` bg):**
- Header: "NETL Directive" badge (primary chip), last modified date, directive title + description
- Scrollable sections from `netl_sections` data
- Conflict indicators: yellow `border-l-4 border-yellow-500` + warning badges for misaligned sections
- Missing section placeholders: `border-2 border-dashed border-error/30`, `error-container/10` bg, error icon, "Missing Corresponding Section" text, "Auto-Generate Section" CTA button

**Right Panel — Gap Analysis Sidebar (320px, `surface-container-low` bg):**
- Header: analytics icon + "GAP ANALYSIS" label (label-md, all-caps, tracking-widest)
- Metrics grid (2-col): Discrepancies count (yellow-600 text), Critical Gaps count (error text)
- Scrollable issue cards:
  - Card: `surface` bg, rounded-xl, shadow-sm, colored border by type
  - Type badge ("Conflict" = yellow, "Missing Info" = red) + reference tag
  - Title (xs, font-semibold) + description (10px, text-muted)
  - Action row: "SUGGEST EDIT" primary button (flex-1) + flag icon button
- Alignment Score: percentage (e.g., 82%), progress bar (`secondary` fill), descriptive label

### 4.4 Floating Toolbar

- Bottom-center, glassmorphism bar (`surface` at 80% opacity, `backdrop-blur-xl`, ambient shadow):
  - "AI Reconciliation" button with sparkle icon
  - Notes button
  - Share button
  - History/Education button
- Rounded-2xl, padding comfortable, items-center

### 4.5 Synchronized Scrolling (Stretch Goal)

- Track visible section in DOE panel via `IntersectionObserver`
- On section change, smooth-scroll NETL panel to the mapped section (and vice versa)

### Files Modified

| Action | File | Purpose |
|--------|------|---------|
| Add | `static/index.html` | Upload view + 3-panel comparison view render functions |
| Modify | `app.py` | New endpoints: sections, gaps, suggest |
| Modify | `directive_extractor.py` | Persist sections alongside report, add section-mapping logic |

---

## Phase 5: Gap Analysis / Editor View

**Goal:** Implement the split-pane editor from `sketch/gap_analysis.html`.

### 5.1 Backend — Edit Suggestion Endpoints

- `POST /api/report/{job_id}/generate-section` — Generate a missing NETL section based on the DOE source:
  - Input: `{ "doe_section_id": "3.0" }`
  - Output: `{ "generated_content": "...", "rationale": "..." }`
- `POST /api/report/{job_id}/apply-suggestion` — Save an accepted suggestion to the draft:
  - Input: `{ "gap_id": "...", "accepted_text": "..." }`
  - Output: `{ "status": "applied" }`
- `GET /api/report/{job_id}/draft` — Return current draft state of the NETL directive with all applied changes

### 5.2 Frontend — Editor Canvas (60% Left Pane)

- Rich text display of the NETL directive with inline highlights for gaps
- Formatting toolbar (bold, italic, bullets, link, undo/redo) — display-only initially, editable as stretch goal
- Highlighted regions with color-coded left borders:
  - Red: critical missing clauses
  - Green: terminology updates
  - Blue: optimization suggestions
- Scroll position synced with gap cards panel

### 5.3 Frontend — Gap Cards Panel (40% Right Pane)

- Scrollable list of gap cards, each containing:
  - **Severity badge:** Critical = red/error, Terminology = green/secondary, Optimization = blue/primary
  - **Title** + description
  - **Suggested addition/change** in a quote-styled block
  - **Action buttons:** "Apply Change" (primary) and "Dismiss" (outline)
- Clicking a card highlights the corresponding region in the editor
- Bottom: Alignment score bar matching comparison sidebar style

### Files Modified

| Action | File | Purpose |
|--------|------|---------|
| Add | `static/index.html` | Gap analysis/editor view render function |
| Add | `app.py` | `POST generate-section`, `POST apply-suggestion`, `GET draft` endpoints |
| Modify | `agents/directives.yaml` | Add prompts for section generation and edit suggestions |

---

## Phase 6: History & Persistence

**Goal:** Add job history, report browsing, and persistent storage.

### 6.1 Backend — History Endpoints

- `GET /api/reports` — List all completed comparison reports:
  ```json
  [
    {
      "job_id": "abc123",
      "doe_directive_id": "DOE O 413.3B",
      "netl_directive_id": "NETL-D-413.3-01",
      "needs_update": "yes",
      "confidence": "high",
      "created_at": "2024-01-15T10:30:00Z",
      "summary": "..."
    }
  ]
  ```
- `DELETE /api/reports/{job_id}` — Remove a report and its associated files
- Migrate from in-memory `jobs` dict to file-backed persistence:
  - Option A: Scan `data/report_*.json` files on startup to populate an index
  - Option B: Add a `data/reports_index.json` manifest file
  - Option C: SQLite database for metadata (best for filtering/sorting)

### 6.2 Frontend — History View

- Table/list of past comparisons with columns:
  - Date, DOE directive, NETL directive, verdict badge, confidence level
- Click any row → navigate to `#/compare/{job_id}` to reopen the comparison
- Filters: date range, verdict (yes/no/uncertain), directive type
- Sort: by date, by verdict, by directive name

### Files Modified

| Action | File | Purpose |
|--------|------|---------|
| Add | `static/index.html` | History view render function |
| Add | `app.py` | `GET /api/reports`, `DELETE /api/reports/{job_id}` endpoints |

---

## Phase 7: Polish & Cross-Cutting Concerns

### 7.1 Design System Compliance Audit

Verify every view against `sketch/DESIGN.md` rules:

- [ ] No 1px solid borders for sectioning (tonal shifts only)
- [ ] Ghost borders (outline-variant at 15% opacity) only where high-contrast accessibility required
- [ ] Public Sans font at all scales with correct letter-spacing
- [ ] Asymmetric grid proportions (sidebar 2-col vs content 10-col)
- [ ] Glassmorphism on floating elements only (toolbar, modals)
- [ ] Color-for-intent: secondary green = success/positive states only
- [ ] Ambient shadows only on floating/modal elements (24–40px blur, 4–6% opacity)
- [ ] No pure black text — use `on-surface` (#1e1b1c)

### 7.2 Responsive Behavior

- **Tablet:** Sidebar collapses to icon-only (48px width), nav labels hidden
- **Mobile:** Sidebar becomes a hamburger-triggered overlay; 3-panel comparison stacks vertically (DOE → NETL → Gaps); bento grid falls back to single-column
- **Breakpoints:** `md` (768px) for tablet, `lg` (1024px) for desktop

### 7.3 Dark Mode

The sketch includes `dark:` Tailwind variants throughout:

- Dark surface colors: `slate-900`, `slate-800`, `slate-950`
- Dark text: `slate-100`, `slate-400`, `blue-300`, `blue-400`
- Toggle button in settings area of TopAppBar
- Persist preference in `localStorage`
- Apply via `class="dark"` on `<html>` element

### 7.4 Accessibility

- Keyboard navigation for all interactive elements (tab order, Enter/Space activation)
- ARIA labels on icon-only buttons (`aria-label="Notifications"`, etc.)
- Focus indicators using ghost-border style (2px primary outline)
- Sufficient contrast ratios verified against WCAG 2.1 AA
- Screen reader announcements for route changes and async updates
- `role="navigation"` on sidebar, `role="main"` on content area

### 7.5 Error Handling & Loading States

- **Skeleton loaders:** Tonal surface rectangles matching the layout (pulsing animation)
- **Toast notifications:** Slide-in from top-right for errors, warnings, and success messages (replacing `alert()` calls)
- **SSE reconnection:** Auto-retry on connection drop with exponential backoff
- **Empty states:** Friendly illustrations/messages when no data (e.g., "No comparisons yet — upload your first pair of directives")

---

## Implementation Priority & Sequencing

| Priority | Phase | Effort | Dependencies |
|----------|-------|--------|-------------|
| **P0** | Phase 1: Foundation (Design System + SPA Shell) | Medium | None |
| **P0** | Phase 4: Comparison Tool (Core Feature) | High | Phase 1 |
| **P1** | Phase 2: Dashboard | Medium | Phase 1 |
| **P1** | Phase 3: Directives Library | Medium | Phase 1 |
| **P2** | Phase 5: Gap Analysis / Editor | High | Phase 4 |
| **P2** | Phase 6: History & Persistence | Low | Phase 1 |
| **P3** | Phase 7: Polish & Cross-Cutting | Medium | All |

### Recommended Build Order

```
Phase 1 (Foundation)
  └─→ Phase 4 (Comparison Tool) ← highest value
        ├─→ Phase 2 (Dashboard)
        ├─→ Phase 3 (Library)
        ├─→ Phase 6 (History)
        └─→ Phase 5 (Gap Analysis Editor)
              └─→ Phase 7 (Polish)
```

Phase 4 (Comparison Tool) is the highest-value deliverable and should be tackled immediately after the foundation. Phases 2 and 3 are lower-risk read-only views that flesh out the navigation. Phase 5 (editor) is the most complex new capability and benefits from having the comparison data model stable first. Phase 7 runs continuously but gets focused attention last.

---

## Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **SPA vs Multi-Page** | Single `index.html` with hash routing (Option B) | No build step, smooth transitions, shared state, simpler deployment |
| **CSS Framework** | Tailwind CDN with inline config | Matches sketches (already using `cdn.tailwindcss.com`), no build step required |
| **Chart Library** | CSS-only bar charts | Minimal dependency; dashboard velocity chart is simple enough |
| **Persistence** | JSON files + index file | Already in use; upgrade to SQLite only if filtering/sorting becomes a bottleneck |
| **Rich Text Editing** | Display-only with "Apply" buttons (Phase 5) | Lower complexity; full contenteditable editor is a stretch goal |
| **Icons** | Material Symbols Outlined (Google Fonts) | Already used in sketches |

---

## Files to Create / Modify

| Action | File | Phase | Purpose |
|--------|------|-------|---------|
| **Rewrite** | `static/index.html` | 1–6 | Full SPA: design system, router, all view render functions |
| **Modify** | `app.py` | 1–6 | New endpoints (dashboard, directives, sections, suggestions, history), SPA catch-all |
| **Modify** | `directive_extractor.py` | 4 | Persist DOE/NETL sections alongside report, add section-mapping logic |
| **Modify** | `agents/directives.yaml` | 5 | Add prompts for section generation and edit suggestions |
| **Create** | `static/styles.css` | 1 | Optional — extracted custom CSS utilities if inline styles grow too large |

> The sketch HTML files in `sketch/` remain as design reference and are not deployed.
