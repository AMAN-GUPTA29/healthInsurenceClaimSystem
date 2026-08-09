# Session Handoff — Resuming on a New Machine

> Read this first when you start a new Claude Code session on the new
> laptop. It's operational (environment setup, current state, known
> gotchas), not architectural — for design decisions and the full
> component inventory, see
> `multi_agent_claims_pipeline/docs/AI_HANDOFF.md`, which explicitly says
> "future AI agents should read this document FIRST before making any
> changes."

## Where things stand (as of 2026-08-09)

- **Phase 0** (foundation/architecture) — complete.
- **Phase 1** (trace/observability infrastructure) — complete.
- **Phase 2A** (claim validation, document verification, cross-document
  validation) — complete, **including** the "Real Document Upload
  Correction" (the first pass wrongly let members pick a document *type*
  from a dropdown instead of uploading a real file; that's fixed — real
  PDF/JPG/PNG upload, `DocumentStorage` abstraction, multipart API, real
  Gemini multimodal classification).
- A follow-up UI bug was also just fixed: `ClaimDetail.tsx` no longer
  shows "Processing…" for documents that will never be processed because
  the pipeline stopped at Claim Validation — it now shows "Not processed"
  with an explanation, derived from real backend fields
  (`claim.stopped_at`, `doc.processing_status`).
- All backend tests (253) and frontend tests (32) pass; `npm run build`
  is clean. Everything is pushed to `origin/main` (commit `330d509`,
  "final phase push").
- **Phase 2B (policy evaluation, financial calculation, final decision)
  has NOT been started** — this was an explicit constraint in the prior
  session and still holds unless you decide otherwise.

## Setting up the new laptop

```bash
git clone https://github.com/AMAN-GUPTA29/healthInsurenceClaimSystem.git
cd healthInsurenceClaimSystem
```

Versions used on this machine (match or exceed):
Python 3.11.9, Node v24.19.0, npm 11.17.0.

**Backend:**
```bash
cd multi_agent_claims_pipeline/backend
python -m venv .venv
.venv\Scripts\activate          # PowerShell — see gotcha below if this fails
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest                # should show 253 passed
```

**Frontend:**
```bash
cd multi_agent_claims_pipeline/frontend
npm install
npm run test                    # should show 32 passed
npm run build                   # should compile clean
```

**`.env` (not committed — you'll need to recreate it):**
Place at `multi_agent_claims_pipeline/.env` (project root, not `backend/`
— see AI_HANDOFF.md Decision 9 for why it must be there specifically).
Copy `.env.example` and fill in a real `GEMINI_API_KEY`:
```bash
AI_PROVIDER=gemini
AI_MODEL=gemini-flash-latest
GEMINI_API_KEY=...              # your real key — never commit this
AI_TIMEOUT_SECONDS=60
DATABASE_URL=sqlite+aiosqlite:///./data/claims.db
LOG_LEVEL=INFO
```

## Known gotchas hit this session

1. **`git` not recognized in PowerShell** — happened on this machine;
   git was installed but not on the PATH PowerShell sessions load. If it
   recurs on the new laptop: check `Get-Command git`, and if that's
   empty, either reinstall Git for Windows (ensure "Git from the command
   line" is selected during setup) or add its `bin`/`cmd` folder to your
   `PATH` environment variable, then open a fresh terminal.
2. **No SQLite migration tooling** — if you change a domain-model column
   and see `sqlite3.OperationalError: table ... has no column named ...`,
   it's because `Base.metadata.create_all()` only creates missing
   tables, never alters existing ones. Fix: delete the local (gitignored)
   `backend/data/claims.db` so it regenerates with the current schema.
   Real data would need a proper migration tool (Alembic) — not needed
   yet for local dev.
3. **PowerShell `.venv\Scripts\activate` blocked by execution policy** —
   hit in an earlier phase on this machine. If it recurs:
   `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` for that
   session, or just use Git Bash (`source .venv/Scripts/activate`)
   instead.

## Source-of-truth files — never modify

`assignment.md`, `policy_terms.json`, `test_cases.json`,
`sample_documents_guide.md` — all at the repo root, one level above
`multi_agent_claims_pipeline/`. Verified unmodified as of the last
commit; keep it that way.

## Next steps if resuming work

Pick up from `multi_agent_claims_pipeline/docs/AI_HANDOFF.md`'s "Next
Phase — Phase 2B / Phase 3" section for what's planned next
(`PolicyEngine`, `FinancialCalculationService`, `DecisionGenerationAgent`,
etc.) — don't start it without confirming that's actually what you want
to do next.
