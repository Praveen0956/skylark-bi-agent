# Skylark BI Agent

A conversational business-intelligence agent over monday.com data (Deals and
Work Orders boards). Founders/execs ask natural-language questions; the
agent pulls live data from monday.com through a small set of generic query
tools, cleans it, computes the real numbers, and answers in plain language
-- no hardcoded CSVs, no stale exports.

Live app: https://skylark-bi-agent-svhf.onrender.com
Repo: https://github.com/Praveen0956/skylark-bi-agent

---

## Approach

The core problem stated in the assignment is that business questions
require manually pulling data from monday.com boards, cleaning
inconsistent formatting, and cross-referencing it by hand. The approach
taken here removes all three manual steps:

1. **Live retrieval** -- a GraphQL client (`monday_client.py`) fetches
   board data directly from the monday.com API on every request (with a
   short cache, see Trade-offs), so the agent is always answering from
   current data, not a snapshot.
2. **Deterministic cleaning** -- `data_normalizer.py` parses every column
   value (dates, currency with symbols/commas/lakh-crore shorthand, status
   casing) into a consistent type before anything else touches it.
3. **Deterministic computation** -- `analysis.py` does all filtering,
   grouping, and aggregation in plain Python. Numbers are computed once,
   correctly, not estimated by a language model.
4. **Conversational interface** -- an LLM (see AI Tools Used) sits on top
   of these three layers purely as an orchestrator: it decides which
   generic tool to call with which arguments, and turns the returned
   numbers into a readable answer. It never does arithmetic itself.

## Architecture

frontend/index.html - minimal chat UI, no build step
backend/app.py - FastAPI server: /api/chat, /api/health
backend/agent.py - LLM tool-calling loop (Gemini)
backend/monday_client.py - GraphQL client for monday.com API v2
backend/data_normalizer.py - cleans messy dates/currency/casing/nulls
backend/analysis.py - deterministic filter/group/aggregate engine


**Flow:** user question -> LLM decides which tool(s) to call -> tool
fetches + normalizes + aggregates live monday.com data -> LLM reasons
over the real numbers -> answer.

**Tool surface** (defined in `agent.py`): four generic primitives compose
to answer most BI questions rather than one hardcoded tool per possible
question:
- `list_columns` -- discover what fields exist on a board
- `list_distinct` -- discover valid values for a filter (e.g. all sectors)
- `query_aggregate` -- filter, group by a column, aggregate a numeric
  column (sum/avg/count/min/max)
- `top_records` -- filter and sort to find the top/bottom N records

## Assumptions

- **Stack**: Python (FastAPI) backend + a lightweight HTML/JS chat
  frontend, single deployable service -- chosen to minimize moving parts
  and deploy failure surface under a same-day deadline.
- **Two boards, fixed roles**: "Deals" (deal funnel data) and "Work
  Orders" (work order tracker data), identified by board ID via
  environment variables, not looked up dynamically.
- **Read-only**: the agent only reads from monday.com; it never writes
  back or modifies board data.
- **Single shared deployment**: no authentication/multi-user support --
  matches the assignment's scope of one deployed instance for evaluation.
- **Exact-match filtering**: filters (e.g. `{"Sector": "Energy"}`) are
  case-insensitive exact matches, not fuzzy/semantic matches. If a filter
  value doesn't exist on the board, the agent is expected to call
  `list_distinct` first to discover valid values rather than guess.

## Trade-offs

- **Generic tool surface vs. per-question tools** -- a small set of
  composable primitives (filter/group/aggregate/top-N) was chosen over
  hardcoding a tool per expected business question, since the exact
  questions users ask aren't known in advance. Trade-off: the LLM
  sometimes needs 2+ tool calls to answer one question (e.g. `list_columns`
  before `query_aggregate`), which costs a bit of latency and, on a free
  API tier, rate-limit headroom.
- **60-second in-memory cache on board data** -- balances freshness
  against monday.com's request latency and rate limits, since a single
  conversation often calls the same board multiple times in a row. A
  longer cache (e.g. 10 minutes) was considered and rejected because the
  assignment emphasizes live data over hardcoded/stale CSVs.
- **LLM never does arithmetic** -- all math happens in deterministic
  Python. This trades a small amount of flexibility (the LLM can't
  freehand a calculation outside the defined tools) for correctness --
  LLMs are unreliable at exact arithmetic across many rows, and a BI tool
  that occasionally hallucinates a total is worse than one that requires
  an extra tool call.
- **In-memory cache, no database** -- board data is fetched live and
  cached only in process memory, not persisted. This means the cache
  resets on every deploy/restart (acceptable, since monday.com is the
  system of record) but also means the free Render tier's spin-down
  after inactivity causes the first request after idle to take ~30-60s
  (cold start), which is a real, visible trade-off during a live demo.
- **Free-tier LLM over a paid one** -- see AI Tools Used below.

## AI Tools Used

- **Claude (Anthropic)** was used throughout the build process itself --
  for architecture design, writing the backend code end-to-end (GraphQL
  client, data normalizer, aggregation engine, tool-calling agent, FastAPI
  server, frontend), debugging deploy issues, and writing this
  documentation.
- **Google Gemini** (`gemini-3.5-flash-lite`) is the LLM used *inside the
  running application* to power the conversational interface -- it's the
  model that receives the user's question, decides which tool to call,
  and writes the final answer. Gemini was chosen over Claude for this
  specific role because its free tier requires no billing setup, which
  mattered for a same-day, no-budget submission. The agent's architecture
  (generic tool surface, deterministic aggregation, caching) is
  provider-agnostic; swapping the LLM provider only touches `agent.py`.

## Challenges Faced

- **Messy source data** -- the Deal Funnel and Work Order Tracker CSVs
  had mixed date formats, currency strings with symbols/commas/lakh-crore
  shorthand, and inconsistent status casing. Solved with a dedicated
  normalization layer (`data_normalizer.py`) applied once per fetch,
  rather than asking the LLM to interpret raw strings per question.
- **GitHub push protection blocked a leaked API key** -- an early commit
  accidentally included a real key inside `.env.example` instead of
  `.env`. Fixed by rotating the exposed key, correcting the file, and
  amending the commit before pushing; added a `.gitignore` entry for
  `.env` to prevent recurrence.
- **LLM model deprecation mid-build** -- `gemini-2.0-flash` was retired
  during development, and its direct replacement (`gemini-2.5-flash`) was
  also unavailable to new API keys. Resolved by migrating to
  `gemini-3.5-flash-lite`, a stable (non-preview) model with a workable
  free-tier rate limit.
- **Free-tier rate limits** -- an earlier candidate model
  (`gemini-3.6-flash`) allowed only 5 requests/minute on the free tier,
  and a single user question can trigger 2+ internal LLM calls (tool
  selection, then answer generation), so it was easy to exhaust the quota
  during testing. Switched to `gemini-3.5-flash-lite`, which has a
  materially higher free-tier limit and is GA rather than preview.
- **LLM tool-call argument malformation** -- the model occasionally
  passed a slightly malformed value for the `board` argument (e.g. extra
  text appended to the expected enum value) rather than a clean
  `"deals"`/`"work_orders"` string. Rather than letting this raise an
  unhandled exception mid-conversation, added a defensive normalization
  step (`_normalize_board`) that extracts the intended board from
  whatever text was passed, so a slightly imperfect tool call degrades
  gracefully instead of failing the whole turn.

## Potential Improvements

- **Model version pinning strategy** -- given how quickly Gemini model
  IDs have been retired/replaced during this project alone, a production
  version of this agent should abstract the model name behind a config
  value (already partially done via `MODEL_NAME` in `agent.py`) with a
  documented fallback chain, rather than a single hardcoded string.
- **Persistent caching / incremental sync** -- replace the in-memory
  60-second cache with a lightweight persistent store (e.g. SQLite or
  Redis) so warm data survives restarts and cold starts on Render's free
  tier don't force a full re-fetch.
- **Fuzzy/semantic filtering** -- current filters are exact-match; a
  production version could support fuzzy matching or let the LLM map
  loose user phrasing ("the energy folks") to the closest valid column
  value automatically via `list_distinct`.
- **Multi-board joins** -- Deals and Work Orders are currently queried
  independently; a natural extension is cross-board questions (e.g.
  "which won deals don't yet have a work order?").
- **Authentication and per-user scoping** -- the current deployment is a
  single shared instance with no login, appropriate for this assignment's
  scope but not for a real multi-user rollout.
- **Streaming responses** -- the chat UI currently waits for the full
  answer; streaming the LLM's response token-by-token would improve
  perceived latency, especially given the cold-start delay on the free
  hosting tier.

---

## Local setup (optional -- not required to deploy)

```bash
cd backend
cp .env.example .env   # fill in the 4 values
pip install -r requirements.txt
uvicorn app:app --reload
```

Visit http://localhost:8000

## Deploy (Render.com)

1. Push this repo to GitHub.
2. Render.com -> New -> Web Service -> connect the repo.
3. It auto-detects `render.yaml`. Add the 4 environment variables in the
   Environment tab (same values as `.env`).
4. Deploy. Check `/api/health` returns `{"status": "ok"}`.

## Environment variables

| Variable | Where to get it |
|---|---|
| `MONDAY_API_TOKEN` | monday.com -> Admin -> API |
| `WORK_ORDERS_BOARD_ID` | Number in the Work Orders board's URL |
| `DEALS_BOARD_ID` | Number in the Deals board's URL |
| `GEMINI_API_KEY` | aistudio.google.com/apikey (free, no billing required) |

See `DECISION_LOG.docx` for a shorter, standalone write-up of the same
reasoning covered above.