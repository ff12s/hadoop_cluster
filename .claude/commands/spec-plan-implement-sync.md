---
name: spec-plan-implement-sync
description: Workflow command scaffold for spec-plan-implement-sync in hadoop_cluster.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /spec-plan-implement-sync

Use this workflow when working on **spec-plan-implement-sync** in `hadoop_cluster`.

## Goal

Drafting a new feature or component via a specification and implementation plan, then synchronizing the plan/spec with code as development proceeds.

## Common Files

- `docs/superpowers/specs/YYYY-MM-DD-*-design.md`
- `docs/superpowers/plans/YYYY-MM-DD-*.md`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Create a specification markdown file in docs/superpowers/specs/ with a date-stamped filename.
- Create a plan markdown file in docs/superpowers/plans/ with a matching date-stamped filename.
- Implement the feature across relevant code/config files.
- Update the plan/spec files to reflect implementation details and synchronize with code changes.

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.