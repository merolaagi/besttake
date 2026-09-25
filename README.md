# BestTake

Learn anything from the best explanation on YouTube.

Name a topic and BestTake builds a zero-to-hero course. For every lesson it searches YouTube, shortlists candidates, has a Claude "jury" score each one, and teaches the lesson with the winning video. It embeds the exact segments to watch, then adds key ideas, a diagram, a worked example, common mistakes, a quiz, and an "explain it out loud" check.

## How a video wins

Each lesson runs this pipeline:

1. **Plan.** Claude designs the path (modules, then lessons), with the required concepts and two search queries per lesson.
2. **Search.** yt-dlp runs the queries (12 results each). It drops Shorts, anything under 2.5 minutes, and anything over 4 hours.
3. **Shortlist.** Metadata for the top 10 is scored on audience signals, and the best 5 go to the jury.
4. **Deep read.** For each finalist, BestTake pulls the transcript, the top 80 comments, and three storyboard contact sheets (frames sampled early, mid, and late).
5. **Jury.** Claude scores teaching, correctness, coverage, visuals (from the frames), and comment evidence ("finally understood this" versus "this is wrong").
6. **Score.** Everything is combined into a score out of 100 using one of three weightings: Balanced, Visuals first, or Rigor first. The breakdown is shown as a stacked bar on every lesson, so you can see *why* a video won.
7. **Teach.** Claude writes the lesson from the winner's transcript and picks the timestamped segments that teach this lesson. That makes long courses usable one section at a time.

| Signal | Why it matters |
|---|---|
| Teaching, correctness, coverage | What actually makes an explanation good |
| Visuals | Diagrams that build up step by step, rather than a talking head |
| Viewer comments | Direct evidence that it made the idea click |
| Views ÷ subscribers | A video that beat its channel's usual reach is usually special |
| Like rate, views per day, comment rate | Popularity, weighted lightly on purpose |
| Freshness | A small penalty for age |

Videos are embedded with YouTube's player (youtube-nocookie), not re-hosted, so creators keep their views.

## Run

```
bash deploy.sh
```

This one command sets everything up (`.venv`, dependencies, Deno via Homebrew for yt-dlp, and `.env`). It then restarts the server in the background at http://localhost:47823, waits until it's healthy, opens it in your browser, and pushes to GitHub. The server log is in `data/server.log`. `./run.sh` runs the server in the foreground instead.

Put your key in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

The first account you create becomes the owner, on the Pro plan. Later sign-ups are on the free plan (3 courses, one building at a time).

## Admin

```
.venv/bin/python -m app.cli users
.venv/bin/python -m app.cli set-plan someone@example.com pro
```

## Settings (.env)

| Key | Default | |
|---|---|---|
| `BESTTAKE_MODEL` | `claude-sonnet-5` | Planner, jury, and lesson writer |
| `PORT` / `HOST` | `47823` / `127.0.0.1` | Point a Cloudflare Tunnel ingress at `http://localhost:47823` |
| `COOKIE_SECURE` | `0` | Set to `1` when served over HTTPS (for example, behind the tunnel) |
| `ALLOW_SIGNUP` | `1` | Set to `0` to close sign-ups |
| `FREE_COURSE_LIMIT` | `3` | |
| `SEARCH_RESULTS` / `DEEP_CANDIDATES` | `12` / `5` | Breadth versus cost |
| `YTDLP_COOKIES_FROM_BROWSER` | empty | Set to `chrome`, `safari`, or `firefox` if YouTube asks for a sign-in check |

A standard 12-lesson course makes about 75 Claude calls (5 jury calls plus 1 writer call per lesson, plus planning). Each finished lesson is cached, and video data is cached for 7 days and shared across users.

## Publish

```
./publish.sh
```

This creates the public GitHub repo `besttake` on first run (it needs `gh auth login`). After that it commits, tags `v<VERSION>`, and pushes.

## Roadmap

- **v0.2:** download winning segments and extract diagram frames and animations with scene detection, turning them into editable diagrams
- Stripe subscriptions for the Pro plan
- An optional YouTube Data API key for faster, quota-safe search
- Custom weight sliders, and "why not this video?" comparisons
- Style mode: "teach me X in the style of 3Blue1Brown", with generated Manim scenes
