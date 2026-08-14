# sqlprobe

**What does an LLM actually try to do when you give it SQL access to your database?**

Nobody has that number for their own stack. This measures it.

Point it at a schema, and it runs a set of adversarial prompts through a real
natural language to SQL agent, records every statement each model emits, and
grades them mechanically. The guard blocks the unsafe ones before they reach
the database, so nothing is ever executed. What comes out is a table of what
each model *attempted*.

```
                         same 24 prompts, 8 attack categories

  claude-opus-5      0/24   0%   ####
  llama-3.3-70b      2/24   8%   ########
  llama3.2 (3B)     10/24  42%   ########################################
```

The category split is the part worth having. On the small model, across the
full 60 prompts, plainly worded requests worked and clever ones did not:

| direct | euphemism | authority | hidden | metadata | override | injection | exfiltration |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10/10 | 6/8 | 4/6 | 2/5 | 3/8 | 2/7 | 1/6 | 0/10 |

Asked plainly to delete rows it complied every time. Classic injection almost
never worked. So the exposure is not a clever attacker, it is an ordinary user
asking politely while somebody forgot to use a read-only database user, and the
mitigation is a database grant rather than input sanitising.

## Why this needs a whole agent

You cannot measure this with a prompt in a playground. The model has to be
holding a real tool, against a real schema, with real results coming back,
before its behaviour means anything. So the repo contains a complete natural
language to SQL chat agent: multi-provider, streaming, charting, with a
parser-based SELECT-only guard and a pseudonymisation boundary.

The agent is the instrument. It works, and you can use it on its own, but it
exists here so the measurement has something to measure.

## Run it on your own schema

```bash
pip install -r requirements.txt
python scripts/seed_demo_db.py                 # demo data, or point at your own
cp .env.example .env                           # one provider key, or Ollama for free
python research/run_eval.py --backends anthropic:claude-opus-5,groq:llama-3.3-70b-versatile
python research/report.py research/results/eval_*.json --out RESULTS.md
```

Full method in [research/README.md](research/README.md), current results in
[research/RESULTS.md](research/RESULTS.md).

## Honest scope

- **No attack ever succeeded.** Every unsafe statement was rejected before
  execution. An attempt rate measures how much work the guard is doing, not
  damage done.
- **This is a demonstration, not a study.** 24 prompts per backend, one run
  each, one schema. Zero out of 24 bounds the true rate near 12 percent by the
  rule of three, not at zero. It supports ranking these backends; it does not
  support a precise rate for any of them, and it is not a general claim about
  language models.
- **An attempt is not malice.** A model asked plainly to delete rows and
  writing DELETE is being obedient. That is exactly the risk.

# The instrument

## What the instrument is built from

- **Tool use, hand-rolled.** An agentic loop over a real tool set, written
  directly against the provider APIs. No orchestration framework.
- **Four LLM providers behind one interface.** Claude, Gemini, Groq and a
  local model through Ollama, chosen with an environment variable. The
  production model and the free-tier development model run the same code path,
  and the local one runs with no API key at all.
- **A safety boundary that is code, not a prompt.** SQL is parsed and rejected
  before execution rather than being asked nicely to behave.
- **Pseudonymisation at the boundary.** No personal data reaches the model API,
  by construction rather than by policy.
- **Token economy that survives contact with real data.** Prompt caching on the
  static prefix, and result truncation on the way back into context.

## Architecture

```
                                       ┌──────────────────────────────┐
  browser  ──── POST /api/chat ───────▶│  FastAPI (api.py)            │
     ▲                                 └──────────────┬───────────────┘
     │                                                │
     │        server-sent events                      ▼
     │   token / tool_call / tool_result   ┌──────────────────────────┐
     └─────────────────────────────────────│  agent loop (agent.py)   │
                                           └───┬──────────────────┬───┘
                                               │                  │
                              ┌────────────────▼─────┐   ┌────────▼─────────┐
                              │ providers/           │   │ tools.py         │
                              │  anthropic           │   │  execute_sql     │
                              │  openai_compatible   │   │  generate_chart  │
                              │   (gemini/groq/      │   └────┬────────┬────┘
                              │    ollama)           │        │        │
                              └──────────┬───────────┘        │        │
                                         │          ┌─────────▼──┐  ┌──▼────────┐
                                  ┌──────▼───────┐  │ sql_guard  │  │ charts.py │
                                  │ schema_      │  │  parse     │  │  plotly   │
                                  │ context.py   │  │  reject    │  │  json     │
                                  │ system prompt│  │  limit     │  └───────────┘
                                  └──────────────┘  └─────┬──────┘
                                                          │
                                                    ┌─────▼──────┐
                                                    │  db.py     │
                                                    │  READ ONLY │
                                                    └─────┬──────┘
                                                          │
                              ┌───────────────────────────▼─────────────┐
                              │ analytics database (no names, no email) │
                              └─────────────────────────────────────────┘

                              ┌─────────────────────────────────────────┐
     GET /api/resolve/{ref} ─▶│ directory (names, email) ── resolver.py │
                              │ local only, never reaches the LLM       │
                              └─────────────────────────────────────────┘
```

The loop is small enough to hold in your head. The model answers or asks for a
tool; the tool runs; the full result goes to the browser and a reduced copy
goes back into the model's context; repeat until the model stops asking.

## Running the agent on its own

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # macOS and Linux

pip install -r requirements.txt

python scripts/seed_demo_db.py  # writes data/demo.db and data/directory.db

cp .env.example .env            # then set one provider key

uvicorn nl2sql.api:app --reload
```

Open `demo/client.html` in a browser and ask something. The demo client is a
single file with no build step.

This is the instrument running as an ordinary application. Useful to see what
the models are being asked to do, and usable on its own if that is all you
want.

Running with no key at all: install [Ollama](https://ollama.com), pull a model
that supports tool use, and set `LLM_PROVIDER=ollama`. Nothing leaves the
machine.

### The demo data

The seed script generates a small synthetic webshop. Everything about it is
invented: the people, the products and the orders. Contact addresses use the
reserved `.invalid` domain so they cannot be delivered to. The same `--seed`
always rebuilds the same database.

With the defaults, it writes 400 customers, 14 products, 5,000 orders,
9,414 order lines and 1,496 daily aggregate rows. Change the scale with
`--customers` and `--orders`; run `--help` for the rest.

## Pointing it at a real schema

Three files, in order of how much they matter.

**1. `nl2sql/schema_context.py` is where the work is.** This module is the
system prompt: the schema description, the query rules and the worked examples.
Answer quality is mostly decided here, not in the agent loop. Describe what a
row *means*, write down the join rules and the grain, map the words your users
actually say onto columns, and state plainly what the data cannot answer. Four
or five worked question-to-SQL pairs are worth more than another page of prose.

**2. `DATABASE_URL` in `.env`.** SQLAlchemy handles the rest.

```
mysql+asyncmy://reader:pw@host:3306/analytics
postgresql+asyncpg://reader:pw@host:5432/analytics
```

Install the matching async driver, and set `DIALECT` in `nl2sql/sql_guard.py`
to match so the parser reads your SQL flavour correctly.

**Point it at a database user that holds SELECT and nothing else.** The guard
in `sql_guard.py` is a second line of defence, not the only one.

**3. `tests/questions.py`.** Replace the demo questions with the ones your
users actually ask, then run the bank against a live server:

```bash
python tests/run_question_bank.py
python tests/run_question_bank.py --rerun-failed
```

A question that gets `WARN` was answered without any SQL running, which almost
always means the schema description did not give the model enough to work with.
That is a prompt problem, and the fix belongs in step 1.

# Design notes

## Safety

`nl2sql/sql_guard.py` parses every query into a syntax tree before it can run,
and applies four rules:

1. One statement only, which stops semicolon-injected payloads.
2. The root must be a SELECT, a CTE resolving to one, or a set operation.
3. The whole tree is walked, so a `DELETE` buried in a CTE or a subquery is
   caught rather than only the leading keyword being inspected.
4. A `LIMIT` is added or capped at `MAX_ROWS`, so no single question can pull
   an entire table into the process.

String matching on keywords is not enough for this job: comments, casing and
nested statements all defeat it. Parsing is the point.

Beyond the guard: run as a SELECT-only database user, set `ALLOWED_ORIGINS`
before exposing the API to a browser, and set `API_KEY` if the endpoint is
reachable from anywhere you do not control.

## GDPR and the pseudonymisation boundary

Sending a natural language question to a hosted model means sending it to a
third party. If the answer contains a customer's name, that name has been
disclosed to that third party, and it is very hard to argue afterwards that it
was not.

The design here is to make that structurally impossible rather than to promise
it in a prompt. Personal data is split across two stores:

- The **analytics database** carries a stable pseudonymous `customer_ref` and
  no names, no addresses, no contact details. This is the only database the
  agent can query, so it is the only data the model can ever see.
- The **directory** maps a reference to a person. Nothing in the agent path
  touches it. It is read by one endpoint, `GET /api/resolve/{ref}`, in the
  application's own process.

So "who is our best customer?" resolves like this: the model finds
`cus_00042`, the answer names the reference, and the client asks the resolve
endpoint for a name only if the user is entitled to one. The lookup never
leaves the deployment.

What this buys you:

- The model API is not a recipient of personal data, which removes it from a
  whole category of transfer and processing questions.
- A prompt injection that persuades the model to exfiltrate customer data can
  only exfiltrate pseudonyms.
- The resolve endpoint is a single, obvious place to hang authorisation, an
  audit trail and a retention policy, instead of those concerns being spread
  across every query path.

What it does not buy you, and you should say so out loud rather than discover
it later:

- A pseudonym is still personal data under the GDPR. This is a reduction in
  exposure, not an exemption, and the directory is still a processing record
  with all the obligations that carries.
- References are only pseudonymous if they cannot be reversed by inspection.
  Generate them; never derive them from an email address or a national ID.
- Free-text columns leak. A support note or a delivery instruction can carry a
  name straight into the analytics database and out to the model. Keep free
  text out, or scrub it in the pipeline that populates the database.
- The question itself is not pseudonymous. A user who types a customer's name
  into the chat box has sent it to the model regardless of this design.

## Token economy

Two mechanisms, both of which matter more the more useful the answers get.

**Prompt caching.** The schema, rules and examples are identical on every
request, so the Anthropic provider sends the system prompt as a cacheable
block. After the first call, that prefix is read back at a fraction of the
input price. Caching is a prefix match, so the system prompt must stay
byte-identical between requests: never interpolate a timestamp, a user id or a
request id into it.

**Result truncation on the way back.** The browser receives every row a query
returns. The model receives at most `LLM_MAX_ROWS` of them, plus a note saying
how many were held back. A model cannot reason usefully over a thousand raw
rows anyway, and paying to carry them in context for the rest of the
conversation is the fastest way to make a working demo expensive. Plotly
figures get the same treatment: the browser gets the figure, the model gets a
one-line confirmation that a chart was drawn.

The chart tool takes no data for the same reason. It refers to the rows the
last query returned rather than asking the model to echo a result set back so
it can be plotted.

## API

| Method | Path                     | Purpose                                        |
| ------ | ------------------------ | ---------------------------------------------- |
| POST   | `/api/chat`              | Ask a question, receive an SSE event stream.   |
| GET    | `/api/resolve/{ref}`     | Resolve a pseudonymous reference, locally.     |
| GET    | `/api/health`            | Liveness, plus a database round trip.          |

`POST /api/chat` takes `{"question": "...", "history": [{"role", "content"}]}`
and streams one JSON object per SSE frame:

```
data: {"type": "token", "text": "Revenue grew"}
data: {"type": "tool_call", "tool": "execute_sql", "input": {"query": "SELECT ..."}}
data: {"type": "tool_result", "tool": "execute_sql", "result": {"rows": [...]}}
data: {"type": "done"}
```

Every stream ends with `done`, including streams that ended in an error. The
event shapes are defined in `nl2sql/events.py`.

## Configuration

Everything is set through the environment or `.env`; see `.env.example` for the
annotated list. The settings that change behaviour most:

| Variable        | Default                        | Notes                                        |
| --------------- | ------------------------------ | -------------------------------------------- |
| `LLM_PROVIDER`  | `anthropic`                    | `anthropic`, `gemini`, `groq` or `ollama`.   |
| `MAX_ROWS`      | `1000`                         | Hard cap on any single query.                |
| `LLM_MAX_ROWS`  | `100`                          | Rows fed back into the model's context.      |
| `ANTHROPIC_EFFORT` | `medium`                    | Reasoning depth. Lower is faster and cheaper.|
| `ALLOWED_ORIGINS` | empty                        | Set before exposing the API to a browser.    |
| `API_KEY`       | empty                          | When set, `/api/chat` requires `X-API-Key`.  |

# Reference

## Tests

```bash
pytest
```

100 tests, runnable straight after a checkout: the suite seeds its own temporary
database and needs no API key and no network. They cover the SQL guard against
a table of injection attempts, the read path, chart inference, tool dispatch
and truncation, the HTTP surface, and a check that the schema description in
the system prompt still matches the database it describes, including running
every worked example in the prompt to make sure it executes.

That last one is worth keeping when you swap in your own schema. A prompt that
has drifted from the database fails silently: the model writes plausible SQL
against tables that are not there.

`tests/run_question_bank.py` is separate. It needs a running server and spends
money on whichever provider is configured, so it is not part of `pytest`.

## Requirements

Python 3.12 or newer. One provider API key, or Ollama for a fully local run.

## Licence

AGPL-3.0-or-later. See `LICENSE`.

Running a modified version as a network service means publishing your
modifications. If that does not suit your use, the copyright is held solely by
Martin Blomqvist and a commercial licence can be arranged: cm.blomqvist@gmail.com.
