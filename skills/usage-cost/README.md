# usage-cost

Report a coding agent's locally recorded token usage, and what that usage would
have cost at published API list rates.

A subscription bills a flat fee, so nothing attaches a price to any individual
request. The tokens are recorded on disk anyway and list rates are published, so
the two combine into one number: **what this period's work would have cost had
it been billed per token through the API.**

That number is an equivalence, not an invoice. It does not predict a bill and it
is not what anyone was charged. It answers a narrower question — how much
metered work went through a flat-rate plan — computed entirely from files
already on the machine, with no network calls.

## What it looks like

```
Runtime: claude-code    Window: all recorded usage
Rate table: 2026-08-16 (USD, list rates)    Days: America/Los_Angeles

Period                          Requests      Tokens          Cost
------------------------------------------------------------------
Today (2026-08-15)                   974      228.5M        232.25
Yesterday (2026-08-14)             1,754      333.9M        435.29
This week (from 2026-08-10)        9,060        1.7B      2,503.72

By model, over the full window:

Model                               Requests          Tokens          Cost
--------------------------------------------------------------------------
Claude Fable 5                        28,615   6,594,388,669     11,226.52
Claude Opus 4.8                        7,348   1,695,400,179      1,707.17
Claude Opus 4.7                        8,516   1,515,189,685      1,453.50
Claude Opus 5                          2,701     702,639,489        533.76
Claude Sonnet 5                        3,260     214,884,210        141.14
Claude Sonnet 4.6                      2,202      81,621,428         76.97
Claude Haiku 4.5                          14         588,882          0.30
<synthetic>                               22               0  not billable
--------------------------------------------------------------------------
Total                                 52,678  10,804,712,542     15,139.36

Excluded as non-billable: <synthetic>.

Read 978 transcript file(s); merged 71,653 repeated record(s) into their requests.
4,580 request(s) had copies whose counts differed; the most complete snapshot of each was used.
```

> The shape of this example is a real run — the model mix, the cache-dominated
> token profile, the duplicate volume — but every count is perturbed by up to
> ±10% so it does not publish an actual usage volume. Costs are recomputed from
> the perturbed counts at the shipped rates, so the arithmetic remains
> self-consistent. Treat it as illustrative, not as a figure to check against.

An at-a-glance block leads, with tokens rounded to B/M/K; the per-model table
below keeps exact counts, because that is the figure worth recomputing by hand.
Passing an explicit window suppresses the block — the window is the same
question asked precisely.

## Run it

```bash
python scripts/usage_cost.py
python scripts/usage_cost.py --since 2026-08-01 --until 2026-08-31
python scripts/usage_cost.py --format json
```

| Flag | Purpose |
| --- | --- |
| `--runtime` | Which runtime's records to read. Default `claude-code`. |
| `--data-root` | Override where those records live. |
| `--since` / `--until` | Restrict to a day range, `YYYY-MM-DD` inclusive. |
| `--tz` | Zone whose calendar days usage is bucketed into. Default `local`. |
| `--rates` | Use a different rate table. |
| `--format` | `table` (default) or `json`. |

Standard library only. Nothing to install, no dependency on any package
index, and the report makes no network calls. (The optional rate-table refresh
described below is the one script that does.)

### Exit codes carry meaning

| Exit | Meaning |
| --- | --- |
| `0` | Report produced; every model was priced. |
| `2` | Usage, configuration, or rate-table error. |
| `3` | Report produced, but a model id had no published rate. Its tokens are counted and shown, its cost withheld, the total marked partial. |
| `4` | The requested runtime has no readable local usage source. |

Exit `3` is a successful run with a caveat, not a failure.

## Choices worth knowing about

**Days follow your local zone.** Records are stamped in UTC, but "today" means
your today, and bucketing in UTC files a US evening's work under tomorrow —
enough to move a third of a day's spend into the wrong row. The zone resolves to
a real IANA zone so each timestamp gets the offset that applied at that moment,
not the one in force today; a frozen offset would misplace winter records by an
hour and can shift them across midnight. The zone is printed in the header and
carried in JSON.

**Repeated records are reconciled, not deduplicated.** The transcript format
appends one API response several times as it streams, under a single
`message.id` / `requestId` pair — and the copies are *not* identical. Early
copies hold partial counts; later ones grow toward the final total. Summing
every line overstates usage badly, and keeping the first copy understates it
just as badly, because an early snapshot of a long response can hold a small
fraction of its final output tokens. Copies are merged per request to the
largest snapshot. In the rare case where copies tie at the same magnitude but
disagree — on how the tokens divide across classes, or on model or speed — the
latest-stamped copy wins; with nothing to order them, the request is dropped
and the drop reported rather than priced by read order. Both the merged count
and the number of requests whose copies actually differed are reported.

**Cache lifetimes are priced apart.** 5-minute and 1-hour cache writes bill at
different multiples of the input rate. The flat total is used whenever the split
carries nothing usable — including when the split field is present but empty —
and is attributed to the cheaper 5-minute tier, so such a record understates
rather than inflates. A split that falls short of the flat total has the
remainder carried at the 5-minute tier and the shortfall counted; one that
claims *more* than the flat total — the multi-iteration record shape, where the
legacy flat field lags the split — is used as stated, with the disagreement
counted.

**Untrustworthy records are excluded and counted.** Every raw counter is
validated before any fallback runs: a present counter must be a non-negative
integer, so corrupt values are rejected rather than masked as zeros, and a
record with no request identity is counted individually rather than merged with
every other identity-less record.

**Unknown model ids are never guessed at.** Resolution is an exact match, then
the same id with a trailing `-YYYYMMDD` removed — nothing matches by prefix or
similarity, so `claude-opus-5-turbo` resolves to nothing rather than to
`claude-opus-5`. A near-miss would produce a confident figure at the wrong rate,
which is worse than reporting the id as unpriced.

**Two totals, two scopes.** The token total counts every record observed; the
cost total covers only priced, billable rows. When they differ the report says
how many of the tokens shown the cost actually covers, because a token count and
a cost on one line otherwise read as the price of those tokens.

**Decimal arithmetic throughout.** Rates are stored as decimal strings, so no
binary floating-point error enters the figure.

**Cache tiers are multiples of the input rate, per model.** The table's
`cache_multipliers` are the default — reads at 0.1x, 5-minute writes at 1.25x,
1-hour writes at 2x — and an entry may override any tier for itself. Claude
Fable 5.1 and Claude Mythos 5.1 read cache at 0.025x input, so a cache-heavy
day on either would be over-priced four times over at the default.

## What it cannot see

- **Only what this machine wrote.** Usage from other machines, other runtimes,
  the web or desktop apps, or sessions whose transcripts were rotated away is
  invisible. The figure is a floor, not an account total.
- **Server-side tools are counted, not costed.** Web search bills per request
  rather than per token; requests are reported separately.
- **Rates can be stale.** `scripts/rates.json` is a versioned data file stamped
  by `rate_table_version`. Wrong rates produce a confidently wrong report, and
  the tests cannot catch that — they pin arithmetic, not rates. The refresh
  script below narrows the window; it does not close it.

## Runtime support

| Runtime | Status |
| --- | --- |
| `claude-code` | Supported — reads local session transcripts. |
| `codex` | Explicitly unavailable; exits `4` with a stated reason. |

Codex is registered as an unavailable adapter rather than omitted, so the
command can say why rather than implying the runtime does not exist. Adding a
runtime means one adapter class plus one registry entry — deduplication,
pricing, and reporting are all runtime-agnostic.

## Prior art

[`ccusage`](https://www.npmjs.com/package/ccusage) (npm, MIT) is the established
tool for reading Claude Code's local usage data and is more featureful for
interactive exploration. This skill is not a reimplementation and takes no
dependency on it. It differs in being agent-instructions-plus-a-script rather
than a CLI a person drives, in pinning its central cost claim to a synthetic
fixture with hand-derived arithmetic, and in treating unknown models and
unsupported runtimes as first-class reported states with their own exit codes.
For browsing personal usage interactively, `ccusage` is the better experience.

## Refreshing the rate table

`scripts/refresh_rates.py` compares the table with the
[models.dev](https://models.dev) catalog (open source, MIT) read from its
repository at one exact commit, and merges new or changed rows with `--write`.
Run it when a model launches; the report itself never fetches anything.

```
$ python scripts/refresh_rates.py --commit f7af17dfaf28e3e2fb7dbf6ca72ba73e8436d59e
anomalyco/models.dev@f7af17dfaf28 (anthropic) vs rates.json (rate_table_version 2026-09-02)
  absent upstream, left as-is: claude-mythos-5, claude-mythos-5-1
  dated aliases covered by their base id: claude-haiku-4-5-20251001, claude-opus-4-5-20251101, claude-sonnet-4-5-20250929
  every catalog row agrees with the table
```

Exit `0` is agreement, `1` is differences found and not written, `2` is a
catalog or table that could not be read or an argument that was refused. The
commit must be a full 40-character SHA; branches, tags, and short SHAs are
refused, and `--write` always needs one to record. Models the catalog does not
list, and the 1-hour cache-write rate it has no field for, stay hand-entered
from the published pricing page — the script reports them and leaves them
alone.

## Tests

```bash
python -m pytest -q fixtures/
```

The central claim is exact and hand-checkable: the synthetic transcript in
`fixtures/data/priced/` comes to exactly $1.2840 at the shipped rates, with the
arithmetic derived by hand in a comment above the assertion. A rate or parsing
change that moves the number fails a test instead of quietly reporting a
different total.

[SKILL.md](SKILL.md) holds the agent-facing instructions, including how results
should be reported back to a person.
