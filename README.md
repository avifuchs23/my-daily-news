# The Morning · הבוקר — your personal daily news agent

A website that rebuilds itself every morning with the top news for you: World & Politics, Tech & AI, Business, and Israel news — Hebrew articles in Hebrew, English articles in English.

**How it works:** every day at ~06:30 (Israel time), a small agent wakes up on GitHub's servers, pulls from a curated pool of Hebrew and English sources, removes duplicate stories, ranks everything against your interest profile (`config.json`), and republishes the site. You just open the same link every morning. Completely free.

---

## Setup (one time, ~10 minutes)

### 1. Create a GitHub account
Go to https://github.com/signup and create a free account.

### 2. Create a repository
1. Click the **+** (top right) → **New repository**
2. Name it `my-daily-news` (or anything you like)
3. Set it to **Public** (required for free GitHub Pages)
4. Click **Create repository**

### 3. Upload these files
1. On the new repository page, click **uploading an existing file**
2. Drag in ALL files from this folder: `build_digest.py`, `config.json`, `template.html`, `requirements.txt`, `index.html`, `digest.json`, `README.md`
3. Click **Commit changes**

**The workflow file must be added separately** (drag-and-drop skips hidden folders):
1. In the repo, click **Add file → Create new file**
2. Name it exactly: `.github/workflows/daily-digest.yml` (type it with the dots and slashes — GitHub creates the folders)
3. Open `daily-digest.yml` from this folder on your computer, copy its contents in, and **Commit changes**

### 4. Turn on the website (GitHub Pages)
1. In the repo: **Settings → Pages** (left sidebar)
2. Under "Source": choose **Deploy from a branch**
3. Branch: **main**, folder: **/ (root)** → **Save**
4. After ~2 minutes your site is live at:
   `https://YOUR-USERNAME.github.io/my-daily-news/`
   (Bookmark it — this is your morning page.)

### 5. Run the agent for the first time
1. Go to the **Actions** tab → if asked, click **"I understand my workflows, enable them"**
2. Click **Daily news digest** (left sidebar) → **Run workflow** → **Run workflow**
3. Wait ~1 minute, refresh your site — real news appears.

From now on it refreshes itself every morning automatically.

---

## Personalizing (edit `config.json` in the browser)

Open `config.json` in your repo → click the ✏️ pencil → edit → Commit. The next run picks it up.

- **Interest weights** — under `"topics"`, raise/lower `"weight"` (0–1) to see more or less of a topic.
- **Muted topics** — add words to `"muted_keywords"` (e.g. `["celebrity", "רכילות"]`) and stories containing them never appear.
- **Boost keywords** — words in `"boost_keywords"` push matching stories up the ranking. Add your city, your industry, companies you follow.
- **Sources** — add/remove any RSS feed in `"feeds"`. Each has a language (`he`/`en`), topics, and a `source_weight` (how much you trust it). Dead feeds are skipped automatically — they never break the site.
- **Story count** — `max_stories`, `max_per_topic`, `max_per_source`.

## The AI upgrade (optional, later)

The agent is AI-ready. To have Claude write crisp 1–2 sentence summaries for every story (in each article's own language):

1. Get an API key at https://console.anthropic.com (typical cost for this: a few dollars/month at most with the Haiku model)
2. In your repo: **Settings → Secrets and variables → Actions → New repository secret**
3. Name: `ANTHROPIC_API_KEY`, value: your key → **Add secret**

That's it — no code changes. The next morning's run detects the key and switches on AI summaries. Remove the secret to switch back to free mode.

## If something breaks

- **Site shows a ⏳ "hasn't refreshed" banner** — a daily run failed. Open the **Actions** tab, click the failed run to see which feed or step caused it. The site keeps showing the last good edition, so nothing is ever lost.
- **A feed died** — the run log says `SKIP <name>`. Replace its URL in `config.json` or delete that entry.
- **Change the update hour** — edit the `cron` line in `.github/workflows/daily-digest.yml`. `"30 3 * * *"` means 03:30 UTC (06:30 Israel summer time).
