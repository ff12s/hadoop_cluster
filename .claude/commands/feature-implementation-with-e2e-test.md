---
name: feature-implementation-with-e2e-test
description: Workflow command scaffold for feature-implementation-with-e2e-test in hadoop_cluster.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /feature-implementation-with-e2e-test

Use this workflow when working on **feature-implementation-with-e2e-test** in `hadoop_cluster`.

## Goal

Adding a new feature/component (e.g., namespace resolver, Airflow container) together with an end-to-end test script to validate integration.

## Common Files

- `docker-compose.yml`
- `tests/test-*.sh`
- `tests/test-*.bat`
- `.gitignore`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Implement the feature (e.g., add new service, resolver, or DAG) in code/config files.
- Add or update docker-compose.yml to wire up the new component.
- Create or update an end-to-end test script in tests/ (e.g., .sh or .bat) to validate the new feature.
- Update .gitignore as needed for new artifacts or mount points.
- Iteratively fix and refine the test script and configs based on test results.

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.