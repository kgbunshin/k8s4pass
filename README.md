# ckad-lab

A hands-on CKAD simulator: you pick a domain, the platform spins up a Kubernetes cluster of your own with N
questions, you solve them in a browser terminal and it grades you. It contains:

- a **question bank** in Markdown + a **validator** that makes sure every question actually works;
- a **web platform** (FastAPI + single page + xterm.js terminal) that creates one session per student.

```bash
./run.sh          # http://127.0.0.1:8000  (Ctrl+C stops it and tears down the clusters)
```

Requirements: `docker`, `kind`, `kubectl` and `python3` (with `venv`). `run.sh` creates the venv and the shell image on first run.

```
questions/<domain>/<id>.md   questions (one per file); _template.md is the model
tools/qlib.py                question parser, lint and variable drawing
tools/validate.py            validates questions against a test cluster
tools/cluster.sh             brings up/tears down the kind cluster used by the validator
app/main.py                  REST API + terminal WebSocket + static files
app/sessions.py              sessions: provision, grade, tear down, reaper
app/terminal.py              WebSocket <-> `docker exec -it` bridge through a PTY
app/static/                  front end (index.html, app.js, style.css, vendor/xterm)
app/shell/                   student shell image (kubectl, vim, tmux, jq, alias k)
tests/                       e2e.py, ui_smoke.py, lifecycle.py
```

## How a session works

1. `POST /api/sessions` draws N questions from the domain (variables are drawn, namespaces never collide).
2. In the background: it creates a `kind` cluster (`ckad-s-<id>`), starts a shell container (`ckad-sh-<id>`)
   and runs each question's `Setup`. The 2h clock starts when it becomes ready (~40s).
3. The browser opens `WS /ws/terminal/<id>`, which does `docker exec -it` into the shell container.
   The student's shell **only sees the session's own cluster** (no docker socket, no capabilities, 256 MB/0.5 CPU).
4. **Finish and grade** (or the time running out: the reaper grades on its own, ~1 min later) runs the `Check`s on
   the host, **out of the student's reach**, and only then reveals checks and solutions. Pass mark: 66%.
5. **Review (go back and fix):** after grading the environment **stays alive** for `REVIEW_MINUTES` (30).
   The result shows up beside the same terminal; clicking a question shows its ✓/✗ checks, the statement and the
   solutions, and **Regrade** (`POST /api/sessions/<id>/regrade`, one question or all) runs the checks again.
   The exam score is the one from the 1st grading; regrades go into the history (`result.history`). Each regrade
   renews the window. A finished-but-alive session still counts toward `MAX_SESSIONS`; "New session" frees it at once.
6. End of the review window (or "New session"): the environment is torn down and the result becomes read-only.
   On startup the server deletes orphan clusters (session state is in memory only).

Environment variables: `MAX_SESSIONS` (default 2, each cluster uses ~1-1.5 GB of RAM), `SESSION_MINUTES` (120),
`REVIEW_MINUTES` (30), `ALLOWED_ORIGINS` (extra origins accepted on POST/WebSocket), `HOST`/`PORT` (in `run.sh`).

### Security: read before exposing

**For now this is local use only** (`127.0.0.1`), by design:

- **No authentication.** The session id (64 bits) is the only secret. Before going on the internet: login, rate limit, per-user quota.
- **kind nodes are privileged containers.** A student who creates a privileged pod in their own cluster can
  escape to the host. Fine for you and friends; for strangers it needs a VM per session or Kata/Firecracker.
- All clusters share the `kind` Docker network.
- The server deletes **every** `ckad-s-*` cluster on startup. Do not run two servers on the same machine.
- Already implemented: `Origin` checked on the WebSocket and on writes (against hijacking by another site, even with a
  valid session), `checks`/solutions only after finishing, reaper with TTL, session limit.

## Question format

A `.md` file with YAML front-matter and `##` sections. See `questions/_template.md`.

| Section | Content |
|---|---|
| front-matter | `id` (= file name), `domain`, `topic`, `difficulty` (1-3), `points`, `namespaces`, `vars` (optional), `check_timeout` (optional) |
| `## Statement` | the task text (Markdown: paragraphs, lists, `code`, ``` blocks) |
| `## Setup` | bash block that prepares the environment |
| `## Check` | one line per check: `points\|description\|command` (exit 0 = correct) |
| `## Solution: name` | bash block that solves it. **At least 2 different solutions** |

- **`vars`** are drawn per session (`{{ns}}`, `{{port}}`...): variation without AI.
- **`namespaces`** is what the validator deletes between runs. `default` and `kube-*` are refused.
- **Checks** verify the outcome (state or behavior), not the way of solving it, and are retried for up to
  `check_timeout` seconds because cluster state converges with a delay. They are one-liners (the command may contain `|`).
- Two different `Solution`s passing the same `Check` prove the check is not brittle.
- Avoid checks that already pass without the student doing anything: they give free points (the validator only warns if **all** of them pass).

## Validating questions

```bash
python3 tools/validate.py --lint-only --strict   # format only, no cluster

tools/cluster.sh up                               # kind cluster 'ckad-val' (~1 min), own kubeconfig
python3 tools/validate.py                         # setup -> checker fails -> solution -> checker passes
python3 tools/validate.py --seeds 3 questions/application-deployment
python3 tools/validate.py --report report.md      # markdown report (PR body)
tools/cluster.sh down
```

A question passes when, unsolved, the checker does **not** give full marks and **each** solution, in a fresh environment,
gives full marks. Exit codes: `0` ok, `1` a question failed, `2` usage/safety error. The validator only accepts a
`kind-*`/`k3d-*` context on localhost, never uses the current kubectl context and runs scripts with a temporary
single-context kubeconfig.

## Tests

```bash
.venv/bin/python tests/e2e.py         # ~2 min: API, defenses, terminal, grading, review/regrade, cleanup (server running)
.venv/bin/python tests/ui_smoke.py    # ~2 min: real front end in its own headless Chrome (server running)
.venv/bin/python tests/lifecycle.py   # ~3 min: automatic expiry, review window and orphans (starts its own server on 8001;
                                      #         stop the normal server first)
```

All three create real kind clusters. `e2e` and `ui_smoke` expect the server at `127.0.0.1:8000`.

## Known limitations and next steps

- Session state is in memory only: restarting the server ends the sessions.
- Images (`busybox`, `nginx`) are pulled from Docker Hub on every new cluster: slow and subject to rate limits.
  Preload with `kind load docker-image`.
- The shell has no `helm` (CKAD covers Helm/Kustomize; `kubectl kustomize` is built in).
- 20 questions covering most CKAD topics (Jobs/CronJobs, multi-container, volumes, rollouts, Kustomize, probes, logs, troubleshooting,
  CRDs, RBAC, quotas, security contexts, Services, EndpointSlices, Ingress, NetworkPolicy). Not covered: Helm (not in the shell image)
  and image builds. NetworkPolicy questions only check the spec, because kind's default CNI may not enforce policies.
- Automatic grading on timeout takes ~1 min after the deadline (reaper interval + grading time).
- Next: `tools/generate.py` (LLM-written questions validated by `validate.py`), a scheduled workflow that opens a PR with
  the report, authentication and strong isolation for the VPS deploy.
