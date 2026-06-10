# Hardware Dashboard UI Redesign Design

Date: 2026-06-11

## Goal

Make the Lumen Hub desktop GUI feel more elegant, refined, and product-grade while preserving the existing PySide application architecture and hardware control behavior.

The approved direction is an advanced hardware dashboard: graphite-black surfaces, subtle glass-like layering, teal status light, amber warning emphasis, and restrained red danger actions. The result should feel closer to a mature hardware control center than a raw engineering tool.

## Current Problems

Evidence from current screenshots of the home, fan, lighting, and LIAN LI pages shows these issues:

- The interface is mostly flat dark gray, so top bar, sidebar, page background, cards, and form fields have weak separation.
- Cards share nearly identical weight and size, so the home page lacks a clear primary focal area.
- Buttons and status chips are usable but visually plain; primary, secondary, warning, and destructive actions need stronger hierarchy.
- Fan and LIAN LI pages use valuable curve editors, but their containers feel utilitarian instead of polished.
- Typography is heavy in many places at once, which makes scanning harder despite large text.
- Navigation works, but it looks like a simple list instead of a product-grade control shell.

## Scope

In scope:

- Rework the global QSS theme in `usb9_lcd/gui/theme.py`.
- Improve the main shell polish in `usb9_lcd/gui/main_window.py`: top bar, sidebar, page chrome, and status presentation.
- Redesign the home page in `usb9_lcd/gui/home.py` into a dashboard-first control center.
- Apply the same visual system to existing fan, lighting, screen, asset, settings, and LIAN LI pages through shared object names and QSS.
- Add lightweight tests that protect the presence of the new style hooks and layout sections.
- Generate offscreen screenshots for home, fan, lighting, and LIAN LI pages after implementation.

Out of scope:

- Hardware protocol changes.
- Fan, OpenRGB, LCD, or LIAN LI write behavior changes.
- Replacing PySide with a web frontend.
- Heavy animation or custom OpenGL effects.
- A full per-page UX rewrite of every control.

## Visual System

The theme should use a controlled hardware-dashboard palette:

- App background: near-black graphite, not pure black.
- Raised panels: layered charcoal with slightly warmer inner surfaces.
- Accent: teal/cyan status glow for connected, active, and primary actions.
- Warning: muted amber for caution and permission states.
- Danger: desaturated red for sleep-all-off and destructive operations.
- Text: high-contrast off-white for values, soft gray-blue for secondary copy.

The style should avoid generic purple-on-dark. It should also avoid excessive neon. Accent colors should be used as status lighting, not decoration everywhere.

Typography should keep Chinese readability first. Use the existing font stack with better hierarchy:

- App title and page title: strong but not oversized.
- Section labels: compact uppercase-like weight where appropriate.
- Metrics: large only for key values, not every status string.
- Hints: softer and smaller, with enough contrast for dark mode.

## App Shell

The shell should become a product frame:

- Sidebar width remains stable, but selected navigation gets a stronger active rail and pill background.
- Top bar should feel like a command deck: app identity left, device/status chips right, danger action separated.
- Page content should sit on a subtly different background from the shell.
- Scrollbars should be thinner and styled to match the dark theme.
- The status bar should remain functional, but visually subdued.

## Home Page Dashboard

The home page should become the most visually intentional page.

Structure:

- A hero/status band at the top with the current mode, LCD device, and system readiness summary.
- A primary metrics row for CPU, GPU, fan, lighting, and LIAN LI state, using differentiated value hierarchy.
- A command dock grouped by purpose: sleep/safety, screen, fan, lighting, LIAN LI.
- A recent events panel that looks like a timeline instead of plain checklist rows.
- Permission and device tree cards remain visible but should not dominate the first view.

The home page should make it obvious within two seconds whether the system is healthy, which hardware is connected, and which action to run next.

## Key Page Styling

Fan page:

- Keep the existing controls and curve editor.
- Make metric cards more compact and visually consistent.
- Give the curve editor a more premium chart container with stronger axis/grid contrast.
- Make disabled write controls clearly disabled without looking broken.

LIAN LI page:

- Preserve the current daily control flow.
- Make target selection, fan curve, lighting, and write lock sections read as separate modules.
- Make the write lock and auto-curve controls visually cautious but not alarming.

Lighting page:

- Keep current OpenRGB flow.
- Improve card grouping, target lists, color swatches, and action buttons through shared theme rules.

Screen/asset page:

- Keep the monitor/assets merge.
- Improve preview cards and tabs through the shared theme.

## Implementation Strategy

Implement in small, reversible layers:

1. Add richer reusable object-name styles in `theme.py`.
2. Adjust shell object names and spacing in `main_window.py` only where needed.
3. Redesign `ControlCenterPage` layout and object names in `home.py`.
4. Add or reuse object names for page sections that need better styling without changing behavior.
5. Add regression tests for dashboard section presence and theme selectors.
6. Generate screenshots and inspect them for layout regressions.

Avoid changing signal connections, hardware operation methods, background workers, or settings serialization unless a visual hook requires a harmless object name or layout wrapper.

## Testing And Verification

Automated checks:

- `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py -q`
- `QT_QPA_PLATFORM=offscreen python -m pytest -q`
- `python -m py_compile usb9_lcd/gui/theme.py usb9_lcd/gui/home.py usb9_lcd/gui/main_window.py`

Visual checks:

- Generate offscreen screenshots for home, screen, fan, lighting, and LIAN LI pages.
- Confirm the home page has a clear hero/status area, stronger card hierarchy, and grouped actions.
- Confirm fan and LIAN LI pages retain visible controls and curve editors.
- Confirm no obvious text clipping at 1180x760 and 1440x900.

## Acceptance Criteria

- The GUI has a coherent advanced hardware-dashboard visual language.
- The home page has a clear focal dashboard area instead of equal-weight status cards only.
- Navigation, top bar, cards, buttons, form fields, tabs, and scrollbars share the same polished style.
- Existing hardware behavior is unchanged.
- GUI tests and full test suite pass.
- New screenshots show a visible improvement over the current flat gray baseline.
