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
cp .env.example .env          # then fill in IG_ACCESS_TOKEN and IG_USER_ID (see below)
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

## Getting your access token and Instagram account id

The Instagram Graph API only works for **professional (Business or Creator)
accounts linked to a Facebook Page**. You only need to do this once
(the token itself needs refreshing about every 60 days).

### 1. Make your Instagram account professional and link a Facebook Page

1. In the Instagram app: **Settings and activity → Account type and tools →
   Switch to professional account**. Creator or Business both work.
2. Link it to a Facebook Page you admin. If you don't have one, create a Page
   at facebook.com/pages/create. Then in Instagram: **Edit profile →
   Page → Connect**, or in Facebook: **Page settings → Linked accounts →
   Instagram**.

### 2. Create a Meta developer app

1. Go to <https://developers.facebook.com/apps> and **Create app**. Register
   as a developer first if asked.
2. Pick the **Other** use case, then app type **Business**.
3. In the app dashboard, add the **Instagram** product and choose
   **API setup with Facebook login**.
4. Note the **App ID** and **App secret** under **App settings → Basic**.

The app can stay in **Development mode**. Because you're the app's admin,
you can use these permissions on your own account without App Review.

### 3. Generate a user access token

1. Open the Graph API Explorer: <https://developers.facebook.com/tools/explorer>.
2. Select your app under **Meta App**, and **User Token** under **User or Page**.
3. Add these permissions:
   - `instagram_basic`
   - `instagram_manage_insights`
   - `pages_show_list`
   - `pages_read_engagement`
   - `business_management` (only needed if your Page belongs to a Business portfolio)
4. Click **Generate Access Token** and approve the Facebook dialog. Make sure
   you tick the Page and Instagram account you want.
5. Copy the token into `.env` as `IG_ACCESS_TOKEN`.

### 4. Exchange it for a long-lived (about 60 day) token

Explorer tokens expire in about an hour. Put `IG_APP_ID` and `IG_APP_SECRET`
in `.env`, then:

```bash
python -m instagram_pipeline exchange-token
```

Replace `IG_ACCESS_TOKEN` in `.env` with the printed token. (You can also use
the **Access Token Debugger** → *Extend Access Token* in the Meta developer tools.)
Repeat this step when the token expires. The tool tells you when the API
rejects your token (error code 190).

### 5. Find your Instagram account id

The id you need is the **Instagram Business Account id** (a long number like
`17841400000000000`). It is not your @handle and not your Page id.

```bash
python -m instagram_pipeline find-account
# Page 'My Page': IG_USER_ID=17841400000000000  (@yourhandle, 4200 followers)
```

Put it in `.env` as `IG_USER_ID`. Under the hood that's
`GET /me/accounts?fields=name,instagram_business_account`.

### Instagram Login tokens

If you got your token through **"Instagram API with Instagram Login"**
(an `IGAA...` token from graph.instagram.com) instead of Facebook Login, set
`IG_GRAPH_HOST=graph.instagram.com` and use the id returned by
`GET https://graph.instagram.com/me?fields=user_id,username`. Reels insights
work the same way. `find-account` and `exchange-token` are for the Facebook
Login flow only.

## Security notes

- `.env`, `data/` and `output/` are git-ignored. `data/reels.json` contains no token.
- The token is sent only to the Graph API host and is redacted from every log
  line and error message.
- If `IG_APP_SECRET` is set, every request carries an `appsecret_proof`, so a
  leaked token alone can't be used from elsewhere if you enable
  "Require App Secret" in your app's advanced settings.

## Development

```bash
python -m unittest -v
```

Tests use a fake HTTP transport and synthetic reels with known "good" slots.
They make no network calls.
