```markdown
# hadoop_cluster Development Patterns

> Auto-generated skill from repository analysis

## Overview

This skill describes the key development patterns, coding conventions, and collaborative workflows used in the `hadoop_cluster` Python codebase. The repository is focused on orchestrating and managing Hadoop clusters, often integrating with Airflow and Docker Compose. The patterns here will help you contribute new features, synchronize documentation and plans, implement robust end-to-end testing, and maintain a clean, consistent codebase.

## Coding Conventions

### File Naming

- **Style:** kebab-case (e.g., `namespace-resolver.py`, `airflow-setup.py`)
- **Example:**
  ```plaintext
  airflow/dags/data-ingestion-dag.py
  airflow/jobs/process-logs-job.py
  ```

### Import Style

- **Relative imports** are used within the package.
- **Example:**
  ```python
  from .utils import parse_config
  from ..common import constants
  ```

### Export Style

- **Named exports** are preferred (explicitly listing what is exported).
- **Example:**
  ```python
  __all__ = ['run_job', 'JobConfig']
  ```

### Commit Messages

- **Conventional commit format** with prefixes:
  - `feat`: New features
  - `fix`: Bug fixes
  - `docs`: Documentation changes
  - `test`: Adding or updating tests
  - `perf`: Performance improvements
- **Average commit message length:** ~71 characters
- **Example:**
  ```
  feat: add namespace resolver for multi-tenant support
  fix: correct mount path for airflow jobs in docker-compose
  ```

## Workflows

### Spec-Plan-Implement-Sync

**Trigger:** When introducing a new feature or component in a structured, documented way  
**Command:** `/new-feature-spec-plan`

1. **Create a specification file** in `docs/superpowers/specs/` with a date-stamped filename.
   - Example: `docs/superpowers/specs/2024-05-01-namespace-resolver-design.md`
2. **Create a plan file** in `docs/superpowers/plans/` with a matching date-stamped filename.
   - Example: `docs/superpowers/plans/2024-05-01-namespace-resolver.md`
3. **Implement the feature** across relevant code and configuration files.
4. **Update the plan/spec files** to reflect implementation details and synchronize with code changes.

---

### Feature Implementation with End-to-End Test

**Trigger:** When adding a new system component and ensuring it works in the integrated environment  
**Command:** `/add-feature-with-e2e-test`

1. **Implement the feature** (e.g., add a new service, resolver, or DAG) in code/config files.
2. **Update `docker-compose.yml`** to wire up the new component.
3. **Create or update an end-to-end test script** in `tests/` (e.g., `.sh` or `.bat`) to validate the new feature.
   - Example: `tests/test-namespace-resolver.sh`
4. **Update `.gitignore`** as needed for new artifacts or mount points.
5. **Iteratively fix and refine** the test script and configs based on test results.

---

### Review-Driven Fix and Plan Sync

**Trigger:** When a review identifies issues or improvements after a feature is merged  
**Command:** `/review-fix-sync-plan`

1. **Apply fixes** to code/config files as per review feedback.
2. **Update `.gitignore` or related config files** if needed.
3. **Synchronize the plan file** in `docs/superpowers/plans/` to match the current state of the code.

---

### DAG Job Addition and Refinement

**Trigger:** When adding a new Airflow DAG and associated job scripts, then refining for maintainability  
**Command:** `/add-dag-job`

1. **Add new DAG Python file(s)** in `airflow/dags/`.
   - Example: `airflow/dags/data-cleanup-dag.py`
2. **Add or update job scripts** in `airflow/jobs/`.
   - Example: `airflow/jobs/cleanup-job.py`
3. **Update `docker-compose.yml` and/or `airflow/Dockerfile`** for correct mounting or copying of job scripts.
4. **Refactor to remove duplication** (e.g., switch from file copy to bind-mount).
5. **Update `.gitignore`** for new or removed files.

---

## Testing Patterns

- **Framework:** Unknown (not detected), but test files are named with the pattern `*.test.ts`.
- **Location:** Typically under a `tests/` directory.
- **Example test file:** `tests/data-ingestion.test.ts`
- **Note:** While the main language is Python, some tests may be written in TypeScript or as shell scripts for end-to-end validation.

---

## Commands

| Command                      | Purpose                                                          |
|------------------------------|------------------------------------------------------------------|
| /new-feature-spec-plan       | Start a new feature/component with spec and implementation plan  |
| /add-feature-with-e2e-test   | Add a feature/component with an end-to-end test script           |
| /review-fix-sync-plan        | Apply post-review fixes and sync the implementation plan         |
| /add-dag-job                 | Add a new Airflow DAG and job scripts, then refine implementation|

```