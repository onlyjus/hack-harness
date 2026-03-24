# Design System Specification: The Architectural Archive

## 1. Overview & Creative North Star
The "Architectural Archive" is the Creative North Star for this design system. Moving away from the generic, boxy layouts typical of government portals, this system treats digital document management as a high-end editorial experience. It balances the authoritative weight of the National Energy Technology Laboratory (NETL) with the precision of a modern laboratory.

We achieve this by breaking the "standard template" look. Instead of rigid grids and 1px borders, we use **Intentional Asymmetry** and **Tonal Layering**. The layout is designed to feel like an organized physical workspace—clean, expansive, and high-utility—where information is separated by depth and light rather than lines.

## 2. Colors & Surface Logic

### The "No-Line" Rule
To maintain a premium, modern aesthetic, **1px solid borders are strictly prohibited** for sectioning. Structural boundaries must be defined solely through background color shifts. For example, a navigation sidebar in `surface-container-low` should sit flush against a `surface` main content area. Differentiation is achieved through the eye’s perception of tonal change, not a literal stroke.

### Surface Hierarchy & Nesting
Treat the UI as a series of stacked architectural materials. Use the following hierarchy to define importance:
- **Level 0 (Base):** `surface` (#fff8f8) – The canvas for the entire application.
- **Level 1 (Subtle Recess):** `surface-container-low` (#fbf1f2) – Used for secondary navigation or background utility bars.
- **Level 2 (The Work Desk):** `surface-container` (#f5eced) – The primary container for document viewers or data tables.
- **Level 3 (Focused Elevation):** `surface-container-highest` (#e9e0e1) – Reserved for active selections or highlighted "DOE vs NETL" comparison panels.

### The "Glass & Gradient" Rule
To add visual "soul" to a government tool, use glassmorphism for floating utility panels (e.g., document filters or floating action toolbars). Use `surface` colors at 80% opacity with a `backdrop-blur` of 12px. For primary CTAs and header backgrounds, utilize a subtle linear gradient from `primary` (#00193c) to `primary-container` (#002d62) at a 135-degree angle to provide depth that flat hex codes cannot achieve.

## 3. Typography
The typography system uses **Public Sans** to provide a highly legible, neutral, and authoritative voice.

*   **Display & Headlines:** Used sparingly to anchor large sections. `display-md` (2.75rem) and `headline-lg` (2rem) should be set with tight letter-spacing (-0.02em) to feel like a high-end technical journal.
*   **Titles:** `title-lg` (1.375rem) is the workhorse for document headers. It provides enough weight to signify a "Source of Truth" (e.g., "NETL Quarterly Report").
*   **Body:** `body-lg` (1rem) is optimized for long-form reading. Line height should be generous (1.6) to reduce eye fatigue during document comparison.
*   **Labels:** `label-md` (0.75rem) uses all-caps with increased letter-spacing (+0.05em) for metadata and tag identifiers, distinguishing technical data from narrative text.

## 4. Elevation & Depth

### The Layering Principle
Depth is achieved through **Tonal Layering**. To highlight a document source, place a `surface-container-lowest` card on top of a `surface-container-low` background. This creates a "soft lift" that feels integrated into the environment.

### Ambient Shadows
When an element must float (like a modal or a context menu), use an ambient shadow.
*   **Blur:** 24px to 40px.
*   **Opacity:** 4% to 6%.
*   **Color:** Use a tinted version of `on-surface` (#1e1b1c). Avoid pure black or grey shadows, which look "muddy."

### The "Ghost Border" Fallback
If high-contrast accessibility is required, use a **Ghost Border**. This is a 1px stroke using the `outline-variant` (#c4c6d1) at **15% opacity**. It should be felt, not seen.

## 5. Components

### Buttons
*   **Primary:** High-contrast `primary` (#00193c) background with `on-primary` (#ffffff) text. Use `xl` (0.75rem) roundedness for a modern, approachable feel.
*   **Secondary:** `secondary-container` (#bdf464) background with `on-secondary-container` (#4b6f00) text. This "energy green" is reserved for successful actions or "Approved" document states.

### Cards & Document Lists
**The Divider-Free Rule:** Prohibit the use of divider lines. Separate list items using `spacing-4` (1rem) of vertical white space or by alternating background tones between `surface-container-low` and `surface-container`.

### Comparison Chips
*   **NETL Source:** Use `primary` (#00193c) with `on-primary`.
*   **DOE Source:** Use `tertiary` (#001b35) with `on-tertiary-container` (#5f99de) for clear visual differentiation in multi-source views.

### Input Fields
Inputs should use `surface-container-highest` with a `Ghost Border` that transitions to a 2px `primary` bottom-only stroke on focus. This mimics the "underlining" of important data in a lab setting.

### The "Source Toggle" (Custom Component)
A specialized component for document comparison. A segmented control using `surface-container-low` as the track and a `surface-container-lowest` "pill" that slides with a subtle ambient shadow to indicate the active data source (NETL vs. DOE).

## 6. Do’s and Don’ts

### Do:
*   **Use Whitespace as Structure:** Use `spacing-12` (3rem) and `spacing-16` (4rem) to separate major functional areas.
*   **Embrace Tonal Shifts:** Rely on the `surface-container` tiers to create hierarchy.
*   **Color for Intent:** Use `secondary` (#466800) strictly for energy-related "success" or "positive" data points.

### Don’t:
*   **Don’t use 100% Black:** Use `on-surface` (#1e1b1c) for text to maintain a softer, more sophisticated contrast ratio.
*   **Don’t use Shadows on Everything:** Only use shadows for elements that physically move or float over the main content (Modals, Tooltips, Popovers).
*   **Don’t use "Default" Grids:** Use an asymmetrical grid where the sidebar is significantly narrower (e.g., 2 columns vs 10 columns) to emphasize the "Archive" feel.