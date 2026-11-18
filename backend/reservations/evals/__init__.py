"""Evaluation suite for the AI restaurant host (#259).

Scenarios (`scenarios/*.json`, loaded by `scenarios_io`) are seeded into an
isolated company each (`seed`) and graded by a deterministic, read-only
`checker` over the database end state.
"""
