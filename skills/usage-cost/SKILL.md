---
name: usage-cost
description: Report a coding agent's locally recorded token usage and what that usage would cost at published API list rates. Use when asked what a subscription period's work would have cost per token, which models or cache tiers dominate spend, or how many tokens an agent has actually used.
---

# Subscription Usage Cost

A subscription bills a flat fee, so nothing attaches a price to any individual
request. The tokens are recorded locally anyway, and list rates are published,
so the two can be combined into one number: **what this period's usage would
have cost had it been billed per token through the API.**

That number is an equivalence, not an invoice. It does not predict a bill, and
it is not what anyone was charged. It answers a narrower question — how much
metered work went through a flat-rate plan — and it is computed entirely from
files already on disk.

## Run it

```bash
python scripts/usage_cost.py
```

```bash
python scripts/usage_cost.py --since 2026-08-01 --until 2026-08-31 --format json
```

| Flag | Purpose |
| --- | --- |
| `--runtime` | Which runtime's records to read. Default `claude-code`. |
| `--data-root` | Override where those records live. |
| `--since` / `--until` | Restrict to a day range, `YYYY-MM-DD` inclusive. |
| `--tz` | Zone whose calendar days usage is bucketed into. Default `local`; also accepts `UTC` or an IANA name. |
| `--rates` | Use a different rate table. |
| `--format` | `table` (default) or `json`. |

A run with no window leads with an at-a-glance block — today, yesterday, and
this week, with tokens rounded to B/M/K — above the per-model detail. An
explicit window suppresses it, since the window is the same question asked
precisely.

**Days follow the reader's local zone, not UTC.** Records are stamped in UTC,
but "today" means the reader's today: bucketing in UTC files a US evening's work
under tomorrow, which is precisely the figure someone is asking about. The week
runs Monday to today, so on a Monday it equals today. The zone in force is
printed in the header and carried in JSON as `timezone` — state it when
reporting, because the same records give different daily totals in different
zones.

The local zone resolves to a real IANA zone (from `TZ`, else `/etc/localtime`)
so each timestamp gets the offset that applied *at that moment* rather than the
one in force today — otherwise a summer machine buckets winter records an hour
off and can move them across midnight. If no IANA zone name can be determined,
days are bucketed directly through the platform's own per-instant rules
(`time.localtime`), which apply the same historical transitions, and the header
labels the zone `system local` — a statement of exactly what is known, rather
than an abbreviation posing as an IANA zone.

Read the exit status before reporting anything:

| Exit | Meaning |
| --- | --- |
| `0` | Report produced; every model was priced. |
| `2` | Usage, configuration, or rate-table error. |
| `3` | Report produced, but at least one model id had no rate. Tokens for those rows are counted and shown; their cost is omitted and the total is marked incomplete. |
| `4` | The requested runtime has no readable local usage source — the directory is missing, holds no `.jsonl` transcripts, or none of them could be read. This is distinct from a readable source containing no usage, which is a real zero and exits `0`. |

Exit `3` is a successful run with a caveat, not a failure. Report the total as
partial and name the unpriced model ids.

## Data source

The `claude-code` adapter reads Claude Code's per-session transcripts —
the `.jsonl` files under `~/.claude/projects` (or `$CLAUDE_CONFIG_DIR/projects`).
Each line is one JSON record; assistant records carry `message.model` and a
`message.usage` block holding the token counts the API returned for that
request. Those counts are what makes a local reconstruction possible.

Fields read, and nothing else:

| Field | Used for |
| --- | --- |
| `message.model` | Rate lookup |
| `message.usage.input_tokens` | Uncached input |
| `message.usage.output_tokens` | Output |
| `message.usage.cache_read_input_tokens` | Cache reads |
| `message.usage.cache_creation.ephemeral_5m_input_tokens` | 5-minute cache writes |
| `message.usage.cache_creation.ephemeral_1h_input_tokens` | 1-hour cache writes |
| `message.usage.cache_creation_input_tokens` | Fallback when the split above is absent |
| `message.usage.speed` | Selects a fast-mode rate where one is defined |
| `message.usage.server_tool_use.web_search_requests` | Reported, not costed |
| `message.id`, `requestId` | Deduplication |
| `timestamp` | Day bucketing, and ordering copies whose pricing identities conflict |

Two properties of the format shape the implementation:

- **Records repeat, and the copies differ.** One API response is appended
  several times as it streams, under a single `message.id` / `requestId` pair.
  Earlier copies hold partial counts and later ones grow toward the final total,
  so the copies are *not* interchangeable. Summing every line overstates usage
  badly — most usage-bearing lines are repeats — while keeping the first copy
  understates it just as badly, because an early snapshot of a long response can
  hold a small fraction of its final output tokens. Copies are therefore
  reconciled per request: the largest aggregate magnitude wins outright.
  Among copies tied at that magnitude, only one disagreement is decidable
  from content — an exact cache-lifetime split supersedes one padded from a
  flat total when model and every other class agree, because the padding is
  this tool's own approximation rather than a source claim. Every other
  disagreement between tied copies — token-class composition, model, or a
  speed variant — is a factual conflict about what to price, settled only by
  source evidence: the uniquely latest timestamp among the contenders. When
  nothing orders them, the request is dropped and the drop reported, because
  pricing it by read order — or by a token-class ordering convention — would
  be arbitrary. Both the number of merged records and the number of requests
  whose copies actually differed are reported, so the reconciliation is
  visible.
- **Cache creation is reported twice.** Once as a flat total, and separately
  split by cache lifetime. The split is preferred because the two lifetimes bill
  at different multiples of the input rate. The flat total is used whenever the
  split carries nothing usable — including when `cache_creation` is present but
  empty — and is attributed to the 5-minute tier, the cheaper of the two, so an
  unsplit record understates rather than inflates. A split accounting for less
  than the flat total has the remainder carried at the 5-minute tier and the
  shortfall counted; a split claiming *more* than the flat total — the
  aggregated multi-iteration record shape, where the legacy flat field lags the
  split, sometimes reading 0 — is used as stated, with the disagreement counted
  so it is visible rather than absorbed.
- **Records that cannot be trusted are excluded, and counted.** Every raw
  counter — including the flat cache total, both split tiers, and server-tool
  counts — is validated before any fallback runs: a counter that is present
  must be a non-negative integer, so a negative, fractional, boolean, or
  string value is rejected rather than masked by normalization; only an
  absent field contributes zero. A record carrying no request identity is
  counted on its own rather than merged with every other identity-less
  record. All of these counts appear in the output.

### What the source does not cover

- **Only what was written locally.** Usage from other machines, other runtimes,
  the web or desktop apps, or sessions whose transcripts were deleted or rotated
  away is invisible. The figure is a floor for the account, not a total.
- **Server-side tool calls are counted, not costed.** Web search bills per
  request rather than per token. Requests are reported separately; the cost
  total covers tokens only.
- **Every request is priced at the synchronous list rate.** The published
  pricing also defines
  [batch processing](https://platform.claude.com/docs/en/about-claude/pricing#batch-processing)
  at 50% of list rates, but a coding agent's requests are interactive — none
  of the local records is a batch request, so the discount is never applied.
- **No session or runtime overhead.** Only per-request token usage is priced.
- **Records with no timestamp are dropped from a windowed run.** They are
  included only when no `--since` / `--until` is given, so a windowed total
  never quietly absorbs usage from outside the window.
- **Absent fields count as zero; present ones must be valid.** A record
  missing a usage field is not an error; the missing component contributes
  nothing. A field that is present but not a non-negative integer makes the
  record invalid — mapping it to zero would make corruption indistinguishable
  from absence. Lines that do not parse, and files that cannot be read, are
  skipped and their counts reported.
- **Subagent turns are included.** They are billed like any other request.

## Rate table

Rates live in `scripts/rates.json`, a versioned data file the report stamps by
`rate_table_version`. Rates are per million tokens, stored as decimal strings so
loading introduces no floating-point error, and all arithmetic is decimal.

Only input and output rates are stored per model. Cache rates are derived, the
way the published pricing defines them:

| Tier | Rate |
| --- | --- |
| Cache read | input x 0.1 |
| Cache write, 5-minute | input x 1.25 |
| Cache write, 1-hour | input x 2.0 |

A model may also carry a `speeds` block for a variant that prices differently,
such as fast mode.

**Model-id resolution is deliberately narrow**: an exact match, then the same id
with a trailing `-YYYYMMDD` release-date suffix removed. Nothing is matched by
prefix or similarity. A near-miss would produce a confident figure at the wrong
rate, which is worse than reporting the id as unpriced — so `claude-opus` and
`claude-opus-5-turbo` both resolve to nothing rather than to `claude-opus-5`.

**An unknown model id never gets a guessed price.** Its tokens are counted and
shown, its cost cell reads `UNPRICED`, it is excluded from the cost total, the
total is marked incomplete, and the command exits `3`.

Model ids listed under `non_billable_models` — locally generated messages that
were never sent to the API — are counted separately and excluded from the cost
total.

**The two totals have different scopes, and the report says so.** The token
total counts every record observed; the cost total covers only priced, billable
rows. When those differ, the run states how many of the tokens shown the cost
actually covers, and `--format json` carries the same split as
`totals.priced_tokens` and `totals.priced_requests`. A token count and a cost
sitting on one line would otherwise read as the price of those tokens.

### Updating it

1. Read the current published rates.
2. Edit `scripts/rates.json`: add or amend entries under `models`, and set
   `rate_table_version` to the date of the change.
3. If a cache multiplier changed, edit `cache_multipliers` — not the per-model
   entries.
4. Run the fixtures. The exact-cost claim is pinned to the shipped rates, so a
   rate change is expected to fail it; re-derive the expected figure by hand and
   update the derivation comment alongside the number.

New model ids are additive: adding one prices previously-unpriced usage without
changing any existing figure.

## Runtime support

| Runtime | Status |
| --- | --- |
| `claude-code` | Supported — reads local session transcripts. |
| `codex` | Explicitly unavailable — no established local usage source, so its usage cannot be measured from this machine. Exits `4`. |

An unsupported runtime returns a stated unavailable reason and a distinct exit
code. It never returns an estimate. A runtime with no readable records is a
gap in what can be measured, and reporting a guess would misrepresent it.

Adding a runtime means adding one adapter in `scripts/runtime_adapters.py` and
one registry entry. An adapter answers two questions — can this runtime be read
here, and what usage does it show — and yields a normalized record per request.
Deduplication, pricing, and reporting are all runtime-agnostic, so a second
adapter needs no changes elsewhere.

## Prior art

Open-source tools already read Claude Code's local usage data; `ccusage` (npm,
MIT) is the established one, and it is more featureful for interactive
exploration — daily and monthly breakdowns, live monitoring, richer terminal
output.

This skill is not a reimplementation of it and takes no dependency on it. It
differs in three ways:

- **Skill form.** It is agent instructions plus a script, so an agent can run it
  and interpret the exit status as part of a larger task, rather than a CLI a
  person drives.
- **Fixture-backed claim.** The exact-cost figure is pinned by a synthetic
  transcript with hand-derived arithmetic, so a rate or parsing change that
  moves the number fails a test instead of quietly reporting a different total.
- **Explicit unknowns.** Unknown model ids and unsupported runtimes are
  first-class reported states with their own exit codes, rather than rows that
  silently read zero.

Use whichever fits. For browsing personal usage interactively, the established
tool is the better experience.

## Reporting results

This skill is read by a person, not piped into another program. The script's
output is the evidence; the reply is the answer. **Do not paste the raw table
and stop** — terminal output does not reliably reach the person on the other
side, and an unexplained wall of figures is not a report.

### Reply to the human in this shape

Lead with the at-a-glance block, then the model breakdown, then only the caveats
the run actually produced.

The values below are placeholders chosen to show the shape. Substitute what the
run actually printed; never copy these figures through.

```markdown
**Today: $12.34** · 10.0M tokens · 100 requests

| Period | Tokens | Cost |
| --- | --- | --- |
| Today (YYYY-MM-DD) | 10.0M | $12.34 |
| Yesterday (YYYY-MM-DD) | 20.0M | $24.68 |
| This week (from YYYY-MM-DD) | 100.0M | $123.40 |

| Model | Requests | Tokens | Cost |
| --- | --- | --- | --- |
| <model> | 60 | 7.0M | $9.00 |
| <model> | 40 | 3.0M | $3.34 |

At list rates, not a bill — a subscription charges a flat fee and prices no
individual request. Rate table <rate_table_version>, days bucketed in <timezone>,
read from <n> local transcript files.
```

Rules for that reply:

- **Reproduce the figures in your message.** Never answer with "see the output
  above" or a bare file path.
- **Abbreviate tokens to B/M/K** in prose and tables. Exact counts belong in the
  script's own detail table, which is the checkable artifact; a reader comparing
  periods wants magnitude.
- **Keep cost exact to the cent.** Rounding money hides the thing being asked
  about.
- **Always state the three qualifiers**: at list rates rather than billed, the
  rate-table version, and the zone days were bucketed in. A cost with no rate
  version and no window is not checkable.
- **Say what was excluded, if anything.** On exit `3` name the unpriced model
  ids and say the total is partial — never present a partial total as complete.
  Mention web search requests when any were recorded, and the duplicate-record
  count only when explaining how the total was reached.
- **Do not editorialize about the amount.** Report it; whether it is high is the
  reader's call.

### Choosing the run

- Asked about recent usage, or asked nothing specific — run with no window. The
  summary block answers today, yesterday, and this week in one pass.
- Asked about a named span — pass `--since` / `--until`. The summary block is
  suppressed for a windowed run, because the window is that question asked
  precisely.
- Feeding another tool, or needing the cache-tier split — use `--format json`.

### Reading the numbers

Observations the report supports, for when the reader asks what drives the
figure. These are readings of the report, not advice; whether anything should
change is the reader's call.

- **Cache reads are the cheap class.** A cache read bills at 0.1x the model's
  input rate, so a large cache-read share means repeated context was re-read
  at a tenth of the price of resending it uncached.
- **Cache writes bill above the input rate** — 1.25x for the 5-minute
  lifetime, 2x for the 1-hour. Writes rivaling reads in the JSON cache split
  suggest the cached prefix keeps changing, so it is being re-written rather
  than re-used.
- **Output is the expensive class per token.** On every model in the shipped
  rate table, output bills at five times the input rate, so an output-heavy
  total is priced by what was generated more than by what was read.

The levers behind these numbers — caching strategy, context management, model
selection — are covered in Anthropic's
[cost-optimization cookbook](https://github.com/anthropics/claude-cookbooks/tree/main/cost_optimization).
