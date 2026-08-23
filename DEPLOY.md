# Deploying Prism (Render + your Namecheap domain)

Prism is a small Python web service. It runs `git` at request time to snapshot/clone the repos it
reviews, so the deploy needs `git` available — the included `Dockerfile` handles that.

> ⚠️ **Read the security note first.** A public Prism will clone/analyze whatever repo a caller
> asks for. Always set **`PRISM_TOKEN`** (and ideally **`PRISM_ALLOWED_REPOS`**) on a public
> deploy, or anyone can make your server clone arbitrary repositories.

---

## Step 1 — Put the code on GitHub

Render deploys from a Git repo. Push this `prism/` folder to a GitHub repo:

```bash
cd prism
git remote add origin https://github.com/<you>/prism.git
git push -u origin dev        # or main
```

## Step 2 — Create the Render service

Easiest is the included Blueprint:

1. Render dashboard → **New → Blueprint** → pick your `prism` repo. It reads `render.yaml` and
   creates a Docker **Web Service** on the free plan.
2. Open the service → **Environment** and set:
   - **`PRISM_TOKEN`** = a long random string (required for a public deploy).
   - `PRISM_ALLOWED_REPOS` = e.g. `github.com/your-org/` (optional but recommended).
   - `GITHUB_TOKEN` = a GitHub token (optional; needed for private repos / to avoid rate limits).
   - `PRISM_DEFAULT_REPO` = a repo/URL to pre-fill the UI (optional).
3. Deploy. You'll get a URL like `https://prism-xxxx.onrender.com`.
   Visit `https://prism-xxxx.onrender.com/?token=<PRISM_TOKEN>` to use it.

*(Prefer no Blueprint? New → Web Service → your repo → Runtime **Docker** → add the same env vars.)*

Free plan notes: the service **sleeps when idle** (first hit after is a ~30–60s cold start), and
its disk is **ephemeral** (the clone cache is rebuilt after a redeploy/sleep). Fine for a demo; for
a heavy repo, use a paid instance and attach a Render Disk mounted at `/tmp/prism-cache`.

---

## Step 3 — Point your domain at it

**Important:** DNS (what Namecheap controls) maps *host names* to servers — it **cannot** route by
path. So `domain.com/prism` is **not** achievable with Namecheap DNS alone. You have two options:

### Option A — `prism.domain.com` (recommended, DNS-only, 5 minutes)

1. Render → your service → **Settings → Custom Domains → Add** `prism.domain.com`.
   Render shows a target like `prism-xxxx.onrender.com`.
2. Namecheap → **Domain List → Manage → Advanced DNS → Add New Record**:
   - Type **CNAME**, Host **`prism`**, Value **`prism-xxxx.onrender.com`**, TTL Automatic.
3. Wait for Render to verify and issue TLS (a few minutes). Done:
   `https://prism.domain.com/?token=<PRISM_TOKEN>`.

### Option B — `domain.com/prism` (needs a proxy in front)

Because DNS can't path-route, something must sit in front of `domain.com` and forward `/prism/*`
to the Render service. The app already supports this: set **`PRISM_BASE_PATH=/prism`** on Render
(the UI uses path-relative URLs, so it just works under the sub-path).

The simplest proxy is **Cloudflare** (free):

1. Move your domain to Cloudflare (Namecheap → set Custom DNS to Cloudflare's nameservers; add the
   site in Cloudflare). Your existing records for `domain.com` keep working.
2. Cloudflare → **Workers & Pages → Create Worker**, paste:

   ```js
   export default {
     async fetch(request) {
       const url = new URL(request.url);
       const ORIGIN = "https://prism-xxxx.onrender.com"; // your Render URL
       return fetch(ORIGIN + url.pathname + url.search, request);
     }
   }
   ```

3. **Workers Routes → Add route**: `domain.com/prism*` → this Worker. Everything else on
   `domain.com` keeps going to its normal origin.
4. On Render set `PRISM_BASE_PATH=/prism`. Visit `https://domain.com/prism/?token=<PRISM_TOKEN>`.

*(If `domain.com` is hosted on Vercel/Netlify instead, use their rewrites — e.g. Netlify
`_redirects`: `/prism/* https://prism-xxxx.onrender.com/prism/:splat 200` — plus
`PRISM_BASE_PATH=/prism`.)*

---

## Using it once deployed

Open the URL (with `?token=…` if you set a token), paste a **local path won't exist on the server**
— so use a **GitHub URL** in the repo box, click **Load PRs**, pick one (or type a **GitHub PR #**),
and **Review**. Set `GITHUB_TOKEN` for private repos.
