# Agent instructions for s2s-mcp

Instructions for any AI coding agent (Claude Code, Codex, OpenCode, etc.) working in this repository.

## Before you start

Read [`skills/s2s/SKILL.md`](skills/s2s/SKILL.md) and the module docstrings in `src/s2s_mcp/`. This repository is a deployment layer over two libraries, [acmadDL](https://github.com/ACMAD-Niamey/acmadDL) and [africas2s](https://github.com/ACMAD-Niamey/africas2s), whose own skills (`skills/acmaddl`, `skills/africas2s` in their repos) define the data conventions, product ids, method names, and statistical discipline rules that everything here relies on.

## The one rule

**No science and no data access live here.** A recipe is a sequence of documented library calls in the order the libraries' own examples use. A pipeline step is a primitive from one of the libraries' MCP servers. If a recipe needs something the libraries cannot do, change the library first (its AGENTS.md says how), release it, bump the pin here.

## Keep the docs and skill in sync

| If you change... | You must update... |
|---|---|
| Tools in `server.py` (names, parameters, return shapes, default surface) | `skills/s2s/SKILL.md` + `README.md` + `tests/test_server.py` (the default-surface and size assertions) |
| `recipes.py` (steps, defaults, outputs of a recipe) | `skills/s2s/SKILL.md` recipe section + `tests/test_recipes.py` |
| `pipeline.py` (reference syntax, destination injection, handleizing, brief output) | `skills/s2s/SKILL.md` pipeline section + `tests/test_pipeline.py` |
| `handles.py` / `tenancy.py` (handle format, tenant modes, env vars, download route) | `README.md` environment table + `tests/test_handles.py` |
| `deploy/` | `README.md` "Running it" |

## Conventions

- Package layout: `src/s2s_mcp/`, Python >= 3.12, `uv` for dependency management. Tests: bare `pytest` must stay fast and offline (acmadDL's network verbs are stubbed in `tests/conftest.py`; africas2s runs for real on synthetic data).
- The default tool surface is deliberately small (a test caps its size). Add a tool only when a recipe or the pipeline cannot express the need; prefer a new recipe over a new primitive.
- Every tool: schema-level parameter descriptions (`Annotated[..., Field(description=...)]`), enums for closed vocabularies, a `Returns:` line, one example, `ToolAnnotations` via the local `tool()` decorator. Re-raise library errors as `ToolError` so agents see the message.
- Outputs are handles, never host paths, in anything an agent sees. Resolve handles against the caller's tenant.
- MCP protocol target: 2026-07-28 (mcp SDK 2.x). Do not add SSE, Roots, Sampling, or Logging; they are deprecated.
