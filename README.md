# Instagram Reels best posting time finder

Pulls your recent Reels and their insights straight from Meta's Instagram Graph
API (plain HTTPS, no SDKs, no third-party services) and works out which
**day of week + hour** slots perform best for *your* account.

- Zero dependencies: Python 3.10+ standard library only.
- Secrets come from environment variables (or a git-ignored `.env`), never from code.
- Raw data is saved locally so you can re-run the analysis without hitting the API.

```
Best times to post Reels for @yourhandle
========================================
Timezone: Asia/Kolkata   Objective: balanced   Recency half-life: 30 days
Reels analysed: 90   Followers: 4,200
Baseline: median reach 1,091, engagement rate 5.2% of reach, reach = 26% of followers

RECOMMENDED SLOTS
  Wednesday 7:00 PM       +62% vs your average   (evidence: 2.0 reels, medium confidence)
  Saturday 7:00 PM        +47% vs your average   (evidence: 5.7 reels, high confidence)
  Sunday 6:00 PM          +20% vs your average   (evidence: 3.1 reels, high confidence)
  ...
WORTH TESTING (promising, but little data yet)
  Wednesday 10:00 PM      +18% vs your average   (evidence: 1.3 reels, low confidence)

BY DAY OF WEEK ...
BY HOUR OF DAY ...
```
*(Example output from synthetic data.)*

## Quick start

```bash
cp .env.example .env          # then fill in IG_ACCESS_TOKEN (see below)
python -m instagram_pipeline run --tz Asia/Kolkata
```

This writes:

| File | What's in it |
| --- | --- |
| `data/reels.json` | Raw reels + insights as fetched (re-usable, no secrets) |
| `output/report.txt` | The human-readable report above |
| `output/recommendations.json` | Recommended slots, day/hour effects, config, warnings |
| `output/reels_scored.csv` | One row per reel: local post time, metrics, weights, scores |

Re-analyse saved data with different settings, without calling the API:

```bash
python -m instagram_pipeline analyze --tz Asia/Kolkata --objective reach --half-life 21
```

## Use it from Claude Desktop (no API key needed)

The pipeline itself uses no LLM. To chat about your results with Claude on your
regular Claude plan (Free, Pro or Max), run it as a **local MCP server**.
Claude Desktop starts it on your computer and calls its tools. Your Instagram
token stays in your local `.env`. Claude only sees the tool results.

1. Clone this repo and fill in `.env` (see the token steps below). Check
   that `python -m instagram_pipeline fetch` works from a terminal first.
2. In Claude Desktop, open **Settings → Developer → Edit Config**. This opens
   `claude_desktop_config.json`. Add:

   ```json
   {
     "mcpServers": {
       "instagram": {
         "command": "python3",
         "args": ["/full/path/to/instagram-analysis-pipieline/claude_desktop_server.py"]
       }
     }
   }
   ```

   On Windows use `"command": "python"` and a path like
   `"C:\\Users\\you\\instagram-analysis-pipieline\\claude_desktop_server.py"`.
   The server reads `.env` from the repo folder. You can instead put
   `IG_ACCESS_TOKEN`, `IG_USER_ID` and `IG_TIMEZONE` in an `"env": {...}`
   block next to `"args"`.
3. Fully quit and reopen Claude Desktop. "instagram" should appear under the
   tools (connectors) menu in the chat box.
4. Ask things like *"When should I post my next reel?"*, *"Refresh my reels
   data and compare reach-only vs engagement-only best times"*, or *"Which of
   my last 20 reels did best for their time slot?"*

Tools it exposes:

| Tool | What it does |
| --- | --- |
| `best_posting_times` | Full recommendation report (timezone, objective, half-life adjustable). Uses cached data, and fetches if there is none. |
| `list_reels` | Your reels with local post time, reach, views, interactions, shares, saves, engagement rate and links. |
| `fetch_reels` | Re-download fresh data from Instagram. |

Cached data and the latest report live in `~/.instagram_pipeline/`. Set
`IG_DATA_DIR` to change that. The server is plain JSON-RPC over stdin/stdout
written against the MCP spec with the standard library (`instagram_pipeline/mcp_server.py`).
If it doesn't show up, check Claude Desktop's MCP logs (Settings → Developer)
and make sure the `python3` it finds is 3.10 or newer.

## CLI options

| Flag | Default | Meaning |
| --- | --- | --- |
| `--tz` | `$IG_TIMEZONE`, else UTC | IANA timezone the recommendations are expressed in. Use your audience's timezone. |
| `--objective` | `balanced` | `reach` (get seen), `engagement` (interactions per view), or `balanced` (both equally). |
| `--half-life` | `30` | Recency weighting in days: a reel this old counts half as much as one posted today. |
| `--lookback-days` | `180` | Only fetch reels from this window. |
| `--min-age-hours` | `48` | Skip reels younger than this; their reach is still climbing. |
| `--prior-strength` | `2` | How many "average" pseudo-reels each slot starts with. Higher = more cautious. |
| `--top` | `5` | How many recommended slots to list. |

## How it decides

1. **Fetch.** `GET /{ig-user-id}/media` (paginated, with insights requested
   inline via field expansion where the API allows it, otherwise one
   `GET /{media-id}/insights` per reel), keeping items whose
   `media_product_type` is `REELS`. Metrics:
   `reach, views, likes, comments, shares, saved, total_interactions,
   ig_reels_avg_watch_time`. (`views` replaced `plays` in Graph API v22; on older
   versions the tool falls back to `plays`, and to per-metric calls if any
   metric is rejected.) It also tries the account-level `online_followers`
   insight, which shows when your followers are online.
2. **Score each reel against your own baseline**, so the question is "did
   timing help?" and not "was this a good reel?":
   - *reach index* = reach / your recency-weighted median reach
   - *engagement index* = (total interactions / reach) / your median rate, with
     the rate smoothed towards your typical rate so a reel with 40 views and 10
     likes can't fake a 25% rate.
   - Scores are log-scaled and capped at about 4.5x so one viral reel can't
     decide the answer by itself.
3. **Weight recent reels more** with an exponential half-life. Weights are
   rescaled to the effective sample size, so "evidence: 3.0 reels" means about
   three reels' worth of data.
4. **Estimate in your local timezone** (DST-aware), sharing strength across neighbouring slots:
   - hour-of-day effect, smoothed across neighbouring hours (a 7:40 PM post
     tells you something about 7 PM and 8 PM),
   - day-of-week effect,
   - each day x hour cell, pulled towards day effect + hour effect. With little
     data a slot stays near your average, and real data moves it.
5. **Recommend** slots with at least ~1.5 reels of evidence that clearly beat
   your average. Strong slots with less evidence go under "worth testing":
   post there a few times and re-run.

Small samples are flagged. With fewer than ~10 reels, treat the output as a
hint for what to test, not a verdict.

## Getting your access token

You need an Instagram **professional account (Creator or Business)**. In the
Instagram app, go to **Settings and activity → Account type and tools → Switch
to professional account**.

There are two ways to get a token. **Option A is easier and needs no Facebook Page.**

### Option A: Instagram Login (no Facebook Page)

1. Go to <https://developers.facebook.com/apps> and **Create app**. Register
   as a developer first if asked. Pick the **Other** use case, then app type **Business**.
2. In the app dashboard, add the **Instagram** product and choose
   **API setup with Instagram login**.
3. Make your account a tester: **App roles → Roles → Add people → Instagram
   Tester**, and enter your @handle. Accept the invite in the Instagram app
   under **Settings → Apps and websites → Tester invites** (on the web:
   instagram.com → Settings → Apps and websites).
4. Back on the **API setup with Instagram login** page, under **Generate access
   tokens**, click **Add account** / **Generate token** next to your account
   and log in. Approve `instagram_business_basic` and
   `instagram_business_manage_insights`.
5. Copy the token (it starts with `IGAA`) into `.env` as `IG_ACCESS_TOKEN`.

You don't need to set anything else. The tool sees the `IG` prefix, talks to
`graph.instagram.com`, and uses your own account (`me`), so `IG_USER_ID` is
optional. Check it with:

```bash
python -m instagram_pipeline find-account
# @yourhandle (MEDIA_CREATOR, 4200 followers): IG_USER_ID=17841400000000000
```

Tokens from the dashboard last about 60 days. Before one expires, extend it with:

```bash
python -m instagram_pipeline refresh-token   # prints a new token for .env
```

The app can stay in **Development mode**. As a tester on your own app, you can
use it on your own account without App Review.

### Option B: Facebook Login (needs a linked Facebook Page)

Use this if your account is already linked to a Facebook Page, or you want
to manage it through Meta Business Suite.

1. Link the Instagram account to a Facebook Page you admin: in Instagram,
   **Edit profile → Page → Connect**, or in Facebook, **Page settings → Linked
   accounts → Instagram**.
2. Create an app as in Option A step 1. Add the **Instagram** product and
   choose **API setup with Facebook login**. Note the **App ID** and **App
   secret** under **App settings → Basic**.
3. Open the Graph API Explorer at <https://developers.facebook.com/tools/explorer>.
   Pick your app and **User Token**, and add `instagram_basic`,
   `instagram_manage_insights`, `pages_show_list` and `pages_read_engagement`
   (plus `business_management` if the Page is in a Business portfolio). Click
   **Generate Access Token** and tick your Page and Instagram account.
4. Explorer tokens expire in about an hour. Put the token in `.env` as
   `IG_ACCESS_TOKEN`, and add `IG_APP_ID` and `IG_APP_SECRET`. Then run
   `python -m instagram_pipeline exchange-token` and replace the token with the
   printed one, which lasts about 60 days.
5. Run `python -m instagram_pipeline find-account`. It lists your Pages and the
   linked **Instagram Business Account id** (a long number, not your @handle or
   Page id). Put it in `.env` as `IG_USER_ID`. This flow requires it.

If the API rejects a token (error code 190), it has expired. Generate a new one.

## Security notes

- `.env`, `data/` and `output/` are git-ignored. `data/reels.json` contains no token.
- The token is sent only to the Graph API host and is redacted from every log
  line and error message.
- With a Facebook Login token and `IG_APP_SECRET` set, every request carries an `appsecret_proof`, so a
  leaked token alone can't be used from elsewhere if you enable
  "Require App Secret" in your app's advanced settings.

## Development

```bash
python -m unittest -v
```

Tests use a fake HTTP transport and synthetic reels with known "good" slots.
They make no network calls.
