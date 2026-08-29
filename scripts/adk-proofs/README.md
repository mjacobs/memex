# ADK proofs for the chat tool policy

Three throwaway scripts that prove the outside-the-code facts
`docs/chat-tool-policy.md` depends on. They use a scripted fake model, so they
make **no Vertex call and cost nothing** — run them after a `google-adk` bump
to check the confirmation API still behaves as the spec assumes.

```bash
uv run python scripts/adk-proofs/confirmation_flow.py   # facts 1-4
uv run python scripts/adk-proofs/policy_predicate.py    # facts 5-7
uv run python scripts/adk-proofs/event_roundtrip.py     # fact 8
uv run python scripts/adk-proofs/minimal_replay.py      # fact 9
```

`minimal_replay.py` is the one that sizes the work: it shows a confirmation
resumes from just the two events that mention the paused call, so chat does not
need durable ADK sessions.

Proved against `google-adk` 2.8.0 on 2026-08-29. `policy_predicate.py`'s FACT 7
is expected to *fail* — it reproduces today's text-only session seeding and
shows a confirmation cannot resume against it.
