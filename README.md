# Skylark BI Agent

A conversational business-intelligence agent over monday.com data (Deals and
Work Orders boards). Ask natural-language questions; the agent calls live
monday.com data through a small set of generic query tools and answers with
real numbers.

## Architecture

```
frontend/index.html   - minimal chat UI, no build step
backend/app.py         - FastAPI server: /api/chat, /api/health
backend/agent.py        - Claude tool-calling loop
backend/monday_client.py - GraphQL client for monday.com API v2
backend/data_normalizer.py - cleans messy dates/currency/casing/nulls
backend/analysis.py     - deterministic filter/group/aggregate engine
```

Flow: user question -> Claude decides which tool(s) to call -> tool fetches
+ normalizes + aggregates live monday.com data -> Claude reasons over the
real numbers -> answer.

See `DECISION_LOG.docx` for the reasoning behind each major design choice.

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
| `ANTHROPIC_API_KEY` | console.anthropic.com -> API Keys |
