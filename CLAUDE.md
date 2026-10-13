# Claude Code handoff — MDA youth platform

> **2026-10-12:** Implementation has started. `CLAUDE_START.md` and the revised `DESIGN_REVIEW.md` supersede conflicting statements below. Implementation decisions: `docs/DECISIONS.md`. Setup: `README.md`.

## Goal

Help Adi Koren design and then build a volunteer scheduling and mentoring product. The current request is architecture and system design. Start with a review and concrete design, then implement one agreed vertical slice at a time. No application code or deployed infrastructure is included in this handoff.

## Read first

1. PRODUCT_REQUIREMENTS.md — product source and latest clarifications.
2. ARCHITECTURE.md — proposed components, boundaries and key flows.
3. DATA_MODEL.md — proposed relational model and integrity rules.
4. OPEN_QUESTIONS.md — unresolved decisions and safe prototype assumptions.
5. DEVELOPMENT_PLAN.md — implementation sequence and acceptance checks.

## Decision authority

Explicit user decisions outrank proposals. Technical choices in these files are recommendations, not approved commitments. Do not silently invent business rules. Ask only when a decision blocks the relevant work; proceed with clearly recorded reversible demo assumptions elsewhere. Explain tradeoffs simply because this is also a portfolio and learning project.

## Product constraints

- Hebrew UI only, RTL, mobile first. Internal code identifiers and documentation may be English.
- Python backend, real persistent database, synthetic data initially.
- Manual scheduling and publication; no automatic recommendations.
- A user may have multiple roles. Home-day membership is separate from roles.
- Enforce authorization in the backend, including guest and mentor time windows.
- Never show drafts to volunteers. Never expose private summaries or even their existence to unauthorized users.
- Invitation UI exists only for active invitations.
- Never claim a phone notification was delivered if only a database row was created.
- Do not introduce real volunteer or patient data during the prototype.

## Working conventions

Inspect the repository before editing and preserve existing conventions. Propose a modular monolith rather than microservices. Use migrations, database constraints and transactions. Keep secrets out of source control. Define meaningful tests for permissions, concurrency and important state transitions. Avoid mock-only flows presented as persistent functionality.

Before implementing a feature, identify its entities, access policy, transaction boundary and failure behavior. Report what works, what was tested and what remains provisional. Do not deploy, contact volunteers or import real data solely because these files describe a future pilot.

## Suggested first message to Claude Code

Read CLAUDE.md and all referenced Markdown files. We are starting with architecture and system design. Summarize confirmed requirements versus proposals, flag contradictions and blockers, and propose the entity model, authorization approach and first end-to-end development slice. Explain the main tradeoffs before implementing the application.
