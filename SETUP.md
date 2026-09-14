# Setup — Entra app registration

The tool signs in **interactively** (delegated) and needs **one Entra app registration** that
carries delegated permissions for **two** APIs:

| API | Scopes | Used for |
|-----|--------|----------|
| **Microsoft Graph** | `ChatMessage.Send`, `Chat.ReadWrite` | Teams channel (send + read chat messages) |
| **Power Platform API** | `CopilotStudio.Copilots.Invoke` (+ `user_impersonation`) | Direct-to-Engine channel |

The app must be a **public client** (mobile & desktop) with redirect URI `http://localhost` so the
MSAL interactive browser flow works.

> **No judge key required for a difference-only run.** A judge model (OpenAI/Foundry) is only needed
> for the *semantic* comparison and *quality grading*. To just prove whether Teams and Direct differ,
> use the **deterministic** path — Run page → turn **Use LLM judge** off, or
> `eval_cli.py --no-llm-judge` — which compares normalized text against `mockllm/model` and needs no
> `OPENAI_API_KEY` / `AZUREAI_OPENAI_*`. You still need the Entra app + sign-in above for the channels.

## Option A — let `setup_app.py` do it

`setup_app.py` is adapted from the original `evals/provisioner.py`, which already creates an Entra
app with the Power Platform / D2E scopes and grants admin consent. This version **also** adds the
Microsoft Graph delegated scopes needed for Teams.

```bash
# Create a brand new app registration (both APIs, admin consent):
uv run python setup_app.py create --name "teams-eval"

# OR enhance an EXISTING app registration with the Graph (Teams) scopes:
uv run python setup_app.py add-graph-scopes --client-id <existing-app-client-id>
```

It writes `AZURE_AD_TENANT_ID` and `AZURE_AD_CLIENT_ID` into your `.env`.

> Running `setup_app.py` itself requires a sign-in with rights to create/modify app registrations
> and grant admin consent (`Application.ReadWrite.All`). It uses the Azure CLI public client via
> device-code login, same as the original provisioner.

## Option B — do it manually in the Azure portal

1. **Entra ID → App registrations → New registration.** Single tenant. Add platform
   **Mobile and desktop applications** with redirect URI `http://localhost`.
2. **API permissions → Add a permission:**
   - **Microsoft Graph → Delegated** → add `ChatMessage.Send` and `Chat.ReadWrite`.
   - **APIs my organization uses → Power Platform API → Delegated** → add
     `CopilotStudio.Copilots.Invoke` (and `user_impersonation`).
     - If Power Platform API isn't listed, register its service principal first
       (app id `8578e004-a5c6-46e7-913e-12f58912df43`).
3. **Grant admin consent** for the tenant.
4. Put the **Directory (tenant) ID** and **Application (client) ID** into `.env` as
   `AZURE_AD_TENANT_ID` / `AZURE_AD_CLIENT_ID`.

## Getting the Teams chat ID

In Teams, open the 1:1 chat with your agent → `...` → **Copy link**. The link looks like:

```
https://teams.cloud.microsoft/l/chat/19:....@unq.gbl.spaces/conversations?context=...
```

Paste the **whole link** into `agents.json` as `teams.chat_link` (the tool extracts the chat ID),
or paste just the `19:...@unq.gbl.spaces` part as `teams.chat_id`.

## Caveats (from the Graph approach)

- **Delegated token only** — app-only tokens can't `POST` chat messages.
- **Graph throttles** chat endpoints — keep runs modest; tune `TEAMS_RATE_DELAY_S`.
- **One shared chat** — every sample posts into the *same* Teams 1:1 thread, and replies are matched
  only by timestamp. So the eval runs **sequentially** (one prompt in flight at a time) and waits
  `TEAMS_TURN_WAIT_S` seconds (default `10`) after each prompt before polling — otherwise concurrent
  prompts flood the chat and answers get mis-attributed. Raise the wait for slow agents
  (Settings → *Turn wait*, the Run page per-run field, or `--teams-wait` on `eval_cli.py`).
- **Responses are HTML** — the tool strips tags before text comparison.
- **Context persists** in a Teams 1:1 thread — the optional `/debug clearstate` /
  `/debug clearhistory` reset commands are best-effort, not a true fresh session.

## Troubleshooting

- **Run shows `Not signed in for audience=powerplatform` (or `=graph`)** — sign-in primes both
  audiences, but an older session may have cached only Graph. Click **Sign in** on the Setup page
  again, then re-run. Eval runs never open a browser themselves (they acquire tokens silently), so
  they fail fast with this message instead of hanging.
- **Run stuck on "Running…"** — click **Cancel** on the Run page to abort the in-flight task and
  re-enable Start. Each channel call is also hard-capped by `CHANNEL_TIMEOUT_S` (default 90s): a
  hung Direct/Teams call is aborted and recorded as a channel error instead of wedging the whole run.
- **Run always freezes at the same point (e.g. `capability repeat=1/3`) only via the web UI** —
  this was a Reflex dev hot-reload bug: `reflex run` watches every top-level folder, so the `.eval`
  file Inspect writes into `logs/` at run start triggered a reload that killed the eval. `rxconfig.py`
  now excludes `logs/` from the watcher (`REFLEX_HOT_RELOAD_EXCLUDE_PATHS`). If you add another dir
  the eval writes into, exclude it the same way. (The standalone CLIs were never affected.)
- **Teams chat flooded with prompts / answers mismatched** — fixed: the eval now runs one sample at
  a time (`max_samples=1`) and waits `TEAMS_TURN_WAIT_S` after each prompt before polling. If a slow
  agent still gets a wrong/missing reply, increase the wait (Run page *Teams turn wait*, Settings, or
  `--teams-wait`). A single shared 1:1 chat can't be parallelised safely.
- **Direct channel 401 / consent error** — confirm the app registration has
  `CopilotStudio.Copilots.Invoke` on the Power Platform API with admin consent granted.
- **Results show every sample as a difference (match_rate 0)** — first check the two channels point
  at the **same agent**. The Direct side uses the Copilot Studio `agent_identifier` (schema name);
  the Teams side uses the chat link. If the Teams reply looks like a totally different bot than the
  test pane, the link and the schema name refer to different agents — fix one so both target the
  same agent. A quick tell: an empty/unconfigured Copilot Studio agent answers Direct with
  "Sorry, I am not able to find a related topic."
- **`Direct start_conversation returned no conversation id`** — handled automatically now: many
  agents return the conversation id only in the `x-ms-conversationid` response header (the opening
  event is just a "typing" indicator), and the client falls back to that. If you still see this, the
  D2E endpoint returned no header and no conversation activity — verify the `environment_id` and
  schema `agent_identifier`.
