---
name: atlas-app-development
description: "Use when changing Atlas. Test, build, and verify."
version: 1.0.0
author: Atlas
license: MIT
metadata:
  hermes:
    tags: [atlas, web-app, frontend, backend, testing]
    related_skills: [test-driven-development, dogfood]
---

# Atlas application development

## When to Use

Use for feature work, fixes, and UI changes in Abdeladim's Atlas application, not for the separate upstream Hermes desktop app.

## Workflow

1. Inspect the current project instructions, package scripts, relevant component/controller/API code, and existing tests before editing. Search for the affected symbol across source and tests before changing a shared interface.
2. For behavior changes, load `test-driven-development` and follow RED→GREEN→REFACTOR. Add an end-to-end check when the project has a real test harness; do not substitute a successful build for behavior verification.
3. Make the smallest cohesive change. Preserve explicit user consent for microphone use, payments, messages, and other consequential actions; show a clear active/stop state for persistent capture.
4. From `C:\Users\JARVIS\Atlas\frontend`, use `npm ci` when dependencies are absent and `npm run build` to typecheck and produce the production UI bundle. The Vite build writes to `backend/atlas_server/static`.
5. Verify the built artifact and, when feasible, exercise the real app in a browser. Check whether the Atlas server is running; a generated bundle does not prove the user's open page has loaded it. If runtime verification is blocked, say exactly what was verified and what still requires reload or launch.
6. Report briefly in French: changed behavior, verification actually run, and the one user action needed to see or use it.

## Reusable details

- For hands-free voice interaction, use `references/voice-interaction.md`.
- Keep test evidence distinct: typecheck/build proves compilation; a real browser interaction proves runtime behavior.
