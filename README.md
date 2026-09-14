# teams-eval

> **Channel-parity evaluation for Microsoft Copilot Studio agents.**
> Prove that your agent answers the **same in Microsoft Teams** as it does in the **test pane**
> (Direct-to-Engine), across a whole testset — and catch the cases where it doesn't.

Built on **[Inspect AI](https://inspect.aisi.org.uk/)**. Drive it from the command line or a
single-page **Reflex** web app.

---

## Why this exists

A Copilot Studio agent that behaves perfectly in the **test pane** can behave differently once it's
published to **Microsoft Teams** — different system context, different rendering (HTML vs plain),
truncated or reordered answers, missing citations/cards, or topics that simply fire differently. The
test pane is *not* a faithful preview of the Teams experience.

`teams-eval` runs the **same prompts through both channels** and tells you, prompt by prompt, whether
the answers agree — on text, on meaning, and on structured content (citations, adaptive cards,
suggested actions). It's a **regression and parity harness** for the "does my published Teams agent
match what I tested?" question.

- **Direct (Direct-to-Engine / D2E)** — the programmatic stand-in for the test pane, via the
  `microsoft-agents-copilotstudio-client` SDK.
- **Teams** — the real published channel, driven through the Microsoft Graph chat API.

---

## Features

- **Dual-channel evaluation** — every prompt is sent to **both** Teams and Direct; one response per
  channel per turn (Teams can emit several messages per turn — they're concatenated into one answer).
- **Three comparison layers**, each independently toggleable:
  1. **Normalized text** — strip HTML → plain, unescape, collapse whitespace, lowercase, exact match.
  2. **Semantic** — an LLM judge decides whether the two answers *mean* the same thing.
  3. **HTML / structured** — body text plus structured parts: **citations**, **adaptive cards**,
     **suggested actions**.
- **Configurable match policy** — choose which layers must agree for a prompt to count as a "match".
- **Repeats + agreement threshold** — run each prompt N times and require a fraction of agreement, to
  absorb non-deterministic answers.
- **Quality grading (optional)** — grade each channel's answer against an expected `target` with the
  judge model.
- **Deterministic mode** — turn the LLM judge off to compare **normalized text only**. No
  OpenAI/Foundry key required — just answer *"do Teams and Direct differ or not?"*.
- **Multi-agent runs** — evaluate one testset against several agents in parallel (each agent has its
  own Teams chat, so it's safe), capped by `MAX_PARALLEL_AGENTS`.
- **Reflex web app** — setup, agents, testsets, settings, running evals, and the parity dashboard in
  one UI on `http://localhost:2009`.
- **Channel-difference dashboard** — side-by-side Direct vs Teams with a **word-level diff**, per-layer
  badges, structured-part chips, an All / Differences / Matches filter, and CSV/HTML export.
- **One-command Entra provisioning** — `setup_app.py` creates (or extends) the app registration with
  the right Graph + Power Platform delegated scopes and grants admin consent.
- **One sign-in, two audiences** — a single interactive browser login primes both Microsoft Graph
  (Teams) and Power Platform (Direct), so eval runs acquire tokens silently and never block mid-run.
- **Robust by design** — hard per-channel timeouts, sequential pacing of the shared Teams chat,
  Graph throttle handling, and a self-healing token cache.

---

## How it works

```
testset.json ──▶ Inspect dataset
                     │
        dual_channel solver ──┬─▶ Direct  (D2E SDK)         ─┐
                              └─▶ Teams   (Graph API)        ─┤  one response per channel
                                                              │
        channel_diff scorer ◀─────────────────────────────────┘
          ├─ normalized-text exact
          ├─ semantic (model judge)
          └─ HTML / structured (citations, cards, suggested actions)
                     │
        metrics + inspect view + side-by-side CSV/HTML report
```

A prompt and **all** the bot messages it produces are treated as **one turn / one response**.

### Comparison layers in detail

1. **Normalized text** — aggressive normalization, then exact equality. The cheapest, strictest layer.
2. **Semantic** — the judge model is asked whether the two answers convey the same meaning, ignoring
   formatting, greetings, and ordering. Returns a `C`/`I` grade with a short explanation.
3. **HTML / structured** — compares the HTML-derived body plus, individually, the set of **citations**
   (anchor hrefs), **adaptive cards** (flattened text leaves), and **suggested actions** (button
   titles). Parts absent in both responses are excluded from the decision.

`MATCH_POLICY` controls which layers must agree (default `normalized,semantic` = match if *either*
agrees). With **repeats > 1**, per-repeat matches are aggregated into an agreement fraction and
compared against the per-case `agreement_threshold` (default `DEFAULT_AGREEMENT_THRESHOLD`, `0.8`).

---

## Prerequisites

- **Python 3.12** and the **[uv](https://docs.astral.sh/uv/)** package manager.
- A **Microsoft Entra (Azure AD) tenant** where you can create an app registration and grant admin
  consent — or an admin who will. The app needs **delegated** scopes on two APIs:
  | API | Scopes | Used for |
  |-----|--------|----------|
  | Microsoft Graph | `Chat.ReadWrite`, `ChatMessage.Send` | Teams channel |
  | Power Platform API | `CopilotStudio.Copilots.Invoke` (+ `user_impersonation`) | Direct channel |
- A **published Copilot Studio agent** reachable through **both** channels:
  - **Direct** — its Copilot Studio **environment ID** + **agent schema name** (e.g. `cr1bd_myAgent`).
  - **Teams** — a **1:1 Teams chat link** with the agent (`...` → *Copy link*).
- **Optional** — an **OpenAI** or **Azure AI Foundry / Azure OpenAI** key, only for the *semantic* and
  *quality* layers. Deterministic (text-only) runs need no key.

See **[SETUP.md](SETUP.md)** for the full app-registration walkthrough (automated and manual) and for
how to find the Teams chat ID.

---

## Quick start

```bash
uv sync                       # install dependencies into .venv
cp .env.example .env          # then fill in the values (see Configuration)
cp agents.example.json agents.json   # then register your agent(s)
```

**1. Provision the Entra app registration** (writes `AZURE_AD_TENANT_ID` / `AZURE_AD_CLIENT_ID` to
`.env`). Either create a new one or extend an existing app with the Teams (Graph) scopes:

```bash
uv run python setup_app.py create --name "teams-eval"
# or:
uv run python setup_app.py add-graph-scopes --client-id <existing-app-client-id>
```

**2. Register your agent(s)** in `agents.json` (copied from `agents.example.json` above) — each
agent's Direct coordinates and Teams chat link:

```json
{
  "my-agent": {
    "direct": { "environment_id": "<env-guid>", "agent_identifier": "<schema-name>" },
    "teams":  { "chat_link": "https://teams.cloud.microsoft/l/chat/19:...@unq.gbl.spaces/..." }
  }
}
```

**3. Run a parity eval** (the `--model` is the **judge** for the semantic/quality layers):

```bash
uv run inspect eval channel_eval.py --model openai/gpt-4o \
    -T agent=my-agent -T testset=testsets/1-test.json

uv run inspect view                   # Inspect's results UI (transcripts, scores, filtering)
uv run python report.py --open        # purpose-built parity dashboard (CSV + HTML) in reports/
```

> The first eval triggers an interactive browser sign-in (priming both audiences); tokens are then
> cached and reused silently.

### Deterministic run — no LLM judge, no key

Compare **normalized text only**, just to prove whether Teams and Direct differ:

```bash
uv run python eval_cli.py --agent my-agent --testset 1-test --no-llm-judge
```

The eval runs **one sample at a time** (the Teams chat is shared — parallel prompts flood it and get
mis-attributed). Pace each Teams turn with `--teams-wait` (seconds to wait after a prompt before
polling; defaults to `TEAMS_TURN_WAIT_S`, `10`):

```bash
uv run python eval_cli.py --agent my-agent --testset 1-test --teams-wait 15
```

---

## Web app (Reflex)

Everything above is also driveable from a single Reflex app — **setup, agents, testsets, settings,
running evals, and the parity dashboard** — on **`http://localhost:2009`**:

```bash
uv run reflex run                     # then open http://localhost:2009
```

Frontend runs on port **2009**, Reflex's internal backend on **2010**. The app reuses the same flat
modules as the CLIs, so both run side by side.

**Pages**

- **Setup** — three ordered steps: **(1) paste session details** — drop in the Copilot Studio
  *Diagnostic info* block + Teams agent link to auto-create an agent (Environment ID + Teams chat
  parsed for you; you add the Direct *schema name*, which diagnostics don't contain); **(2) app
  registration** — create the Entra app or add Graph (Teams) scopes to an existing one; **(3) sign
  in** — interactive browser login priming **both** audiences. Do steps 1–2 before signing in.
- **Agents** — add/edit/delete entries in `agents.json`; Teams chat links are parsed + validated.
- **Testsets** — create/edit `testsets/*.json` (per-case `id`, multi-turn `input`, `target`, `tags`,
  `repeats`, `agreement_threshold`). **Download** exports a testset as JSON; **Import** adds one
  (validated + name-sanitized).
- **Settings** — edit every `.env` knob (comparator toggles, match policy, repeats, Teams polling,
  turn wait, reset commands, judge model + Foundry/OpenAI endpoint & key).
- **Run** — pick one or more agents + a testset + judge + quality grading; runs in the background with
  live per-sample progress and a per-agent status badge. Selected agents run **in parallel** (capped by
  `MAX_PARALLEL_AGENTS`). **Cancel** aborts all in-flight runs. Turn **Use LLM judge** off for a fully
  deterministic, key-free run. Set **Teams turn wait (s)** per run.
- **Results** — the channel-difference dashboard (word-diff, per-layer badges, structured chips,
  All/Differences/Matches filter, CSV/HTML export) over any past run in `logs/`. A multi-agent run opens
  a **combined view** with an **agent** column and a per-agent matched/total summary.

---

## Results & reporting

- **`inspect view`** — Inspect AI's bundled viewer. Browse every sample, both channels' transcripts,
  scores, and filter pass/fail. The general-purpose results UI.
- **`report.py`** — a self-contained **channel-difference dashboard** (no extra deps). Each row shows
  Direct vs Teams side-by-side plus a **word-level diff** (red = only in Direct, green = only in
  Teams), per-layer agreement badges, and structured-part chips. `--open` opens it in your browser;
  output is also written as CSV.

```bash
uv run python report.py                       # latest log → reports/
uv run python report.py --log logs/<run>.eval --out-dir reports --open
```

---

## Configuration

| Where | What |
|-------|------|
| `agents.json` | Per-agent **Direct** (`environment_id`, `agent_identifier`) + **Teams** (`chat_link` or `chat_id`). Supports **multiple agents**. Git-ignored — see `agents.example.json`. |
| `.env` | Auth, polling, repeats/threshold, comparator toggles, match policy, judge model + key. See `.env.example`. |
| `testsets/*.json` | `cases[]` with `id`, `input`, optional `target`, `tags`, `repeats`, `agreement_threshold`. |

### Testset format

`input` is either a **string** (single turn) or a list of **turns** (`{"role": "user", "content": ...}`);
only user turns are sent. `target` (optional) is the criteria the quality grader checks against.

```json
{
  "name": "smoke",
  "cases": [
    {
      "id": "greeting",
      "input": "Hi, who are you and what can you do?",
      "target": "",
      "tags": ["smoke"],
      "repeats": 1,
      "agreement_threshold": 1.0
    }
  ]
}
```

### Key `.env` settings

| Variable | Default | Purpose |
|----------|---------|---------|
| `AZURE_AD_TENANT_ID` / `AZURE_AD_CLIENT_ID` | — | Entra app for interactive sign-in (set by `setup_app.py`). |
| `JUDGE_MODEL` | `openai/gpt-4o` | Inspect model string for semantic comparison + quality grading. |
| `OPENAI_API_KEY` / `AZUREAI_OPENAI_*` | — | Judge provider credentials (only for LLM layers). |
| `COMPARE_SEMANTIC` / `COMPARE_STRUCTURED` / `COMPARE_CITATIONS` / `COMPARE_CARDS` / `COMPARE_SUGGESTED` | `true` | Toggle each comparison layer/part. |
| `MATCH_POLICY` | `normalized,semantic` | Which layers must agree for a match. |
| `DEFAULT_REPEATS` / `DEFAULT_AGREEMENT_THRESHOLD` | `1` / `0.8` | Non-determinism handling. |
| `TEAMS_TURN_WAIT_S` | `10` | Wait after a Teams prompt before polling for the reply. |
| `TEAMS_POLL_INTERVAL_S` / `TEAMS_POLL_TIMEOUT_S` / `TEAMS_QUIET_WINDOW_S` / `TEAMS_RATE_DELAY_S` | `1.5` / `60` / `4` / `1.0` | Teams polling + throttle pacing. |
| `CHANNEL_TIMEOUT_S` | `90` | Hard per-channel timeout — a hung call is recorded as a channel error. |
| `TEAMS_RESET_COMMANDS` | — | Optional best-effort `/debug` reset commands sent before each sample. |
| `MAX_PARALLEL_AGENTS` | `3` | Cap on agents evaluated in parallel. |
| `LOG_LEVEL` / `LOG_FILE` | `INFO` / `logs/teams-eval.log` | Logging (rotating file sink). |

> **Already handled for you:** `agents.json` (real tenant/environment/Teams IDs), `.env`, the token
> cache, `logs/`, `reports/`, and Reflex runtime dirs are all **git-ignored**. The committed
> `agents.example.json` documents the structure — a fresh clone copies it to `agents.json` (or adds
> agents via the web app). Nothing sensitive is tracked.

---

## Project structure

```
teams-eval/
├── channel_eval.py      # Inspect task: dataset, dual_channel solver, channel_diff + quality scorers, metrics
├── compare.py           # ChannelResponse model, normalizers, the three comparators, judge helpers
├── direct_client.py     # Direct-to-Engine channel client (Copilot Studio SDK)
├── teams_client.py      # Teams channel client via Graph (send + poll + multi-message + /debug reset)
├── auth.py              # MSAL token provider — interactive login, cached, two audiences
├── setup_app.py         # Create/extend the Entra app registration (Graph + Power Platform scopes)
├── eval_cli.py          # Subprocess entry point for a single eval (used by the UI and for CLI runs)
├── report.py            # Visual channel-difference dashboard (word-level diff, filters) + CSV
├── log_setup.py         # Central loguru configuration
├── rxconfig.py          # Reflex config (ports 2009/2010, hot-reload excludes for logs/)
├── agents.json          # Per-agent Direct + Teams coordinates (git-ignored; see agents.example.json)
├── testsets/            # Testset JSON files
├── ui/                  # Reflex web app
│   ├── ui.py            # App entry — registers pages/routes
│   ├── pages/           # setup, agents, testsets, settings, run, results
│   ├── services/        # config_io, diagnostics, runner, logstream
│   └── components/      # layout + widgets
└── tests/               # Pytest suite
```

---

## Testing

```bash
uv run pytest             # run the suite
uv run ruff check .       # lint
uv run ruff format .      # format
```

---

## Tech stack

- **Python 3.12**, **uv**, **Ruff**, **Pytest**
- **[Inspect AI](https://inspect.aisi.org.uk/)** — evaluation framework (datasets, solvers, scorers,
  metrics, judge models, viewer)
- **microsoft-agents-copilotstudio-client** — Direct-to-Engine channel
- **MSAL** — delegated interactive auth (two audiences)
- **httpx** + **BeautifulSoup** — Graph calls + HTML extraction
- **Reflex** — the web app (pure-Python full-stack)
- **pydantic**, **loguru**, **typer**, **python-dotenv**, **openai**

---

## Troubleshooting

Common issues — full list in **[SETUP.md](SETUP.md#troubleshooting)**:

- **`Not signed in for audience=powerplatform` (or `=graph`)** — sign in again on Setup; it primes
  both audiences. Eval runs never open a browser themselves (they fail fast instead of hanging).
- **Run stuck on "Running…"** — click **Cancel**. Each channel call is also hard-capped by
  `CHANNEL_TIMEOUT_S`.
- **Every sample shows as a difference (match_rate 0)** — check both channels point at the **same
  agent**: Direct uses the schema `agent_identifier`, Teams uses the chat link. An empty Copilot
  Studio agent answers Direct with *"Sorry, I am not able to find a related topic."*
- **`Direct start_conversation returned no conversation id`** — handled automatically (the client
  falls back to the `x-ms-conversationid` header). If it persists, verify the `environment_id` and
  schema `agent_identifier`.
- **Teams chat flooded / answers mismatched** — the eval runs one sample at a time and waits
  `TEAMS_TURN_WAIT_S` after each prompt. Increase the wait for slow agents.

---

## License

Released under the [MIT License](LICENSE).
