# The yt-artist Development Journey: Building a CLI Tool with Human-AI Collaboration

*A record of iterative, collaborative development between a human developer and Claude across 5 sessions.*

---

## The Starting Point

yt-artist began as a Python CLI tool for fetching YouTube channel video URLs, transcribing videos (via yt-dlp subtitles), and generating AI-powered summaries using local Ollama or OpenAI-compatible APIs. The initial codebase had the core data model (SQLite with artists, videos, transcripts, prompts, summaries), basic CLI commands, and an MCP server for IDE integration.

The code worked, but it was rough. We set out to make it production-quality through a series of focused sessions.

---

## Session 1: Foundation Audit and Portability

**Focus:** Code quality, portability, test reliability.

**What happened:**
- Deep audit of the entire codebase: storage, CLI, transcriber, summarizer, fetcher, LLM client.
- Found and fixed portability issues (hardcoded paths, macOS assumptions that broke on other systems).
- Fixed test reliability issues (tests that depended on execution order or global state).
- Improved error messages and edge case handling.

**Result:** 81 tests passing. The codebase went from "it works on my machine" to "it works reliably and tests prove it."

**Lesson learned:** Starting with a thorough audit before adding features pays enormous dividends. Every subsequent session benefited from the solid test foundation.

---

## Session 2: User Frustration Audit and UX

**Focus:** Making the tool pleasant to use.

**What happened:**
The human asked a pointed question: *"What would frustrate a real user?"* This shifted the focus from code quality to user experience.

We identified and fixed 6 frustration points, then did a broader UX/performance audit that led to 9 improvements:
- LLM client caching (don't reconnect on every summarize call)
- DB fast-path for artist/video lookup (skip yt-dlp when data exists)
- Transcript truncation (don't send 100K chars to the LLM)
- Default prompt created on fresh DB (so summarize works immediately)
- Better error messages, version flag, search header formatting

**Result:** 99 tests passing. The tool went from "functional" to "thoughtful."

**Key insight from the human:** *"Think about what would frustrate someone using this for the first time."* This reframing was more valuable than any technical improvement. It taught me to evaluate features from the user's chair, not the developer's.

---

## Session 3: Parallelism

**Focus:** Speed for bulk operations.

**What happened:**
- Audited which operations were I/O-bound and would benefit from parallelism.
- Implemented `ThreadPoolExecutor` for bulk transcribe and summarize.
- Added batch DB queries to skip already-processed videos.
- Added speculative subtitle download (try auto-subs first).
- Thread-safe progress counter with locking.

**Result:** 109 tests passing. Bulk operations ~2x faster with conservative default concurrency.

**Design principle:** Conservative defaults (2 workers), aggressive configurability (env vars for everything). Users who know what they're doing can crank it up; new users are safe by default.

---

## Session 4: Rate-Limit Safety + Guided Onboarding

**Focus:** Two seemingly unrelated concerns that both serve new users.

### Rate-limit safety
- Research into YouTube's rate-limiting behavior.
- Added sleep flags, inter-video delays, max concurrency caps.
- Optimistic subtitle download with fallback.
- Cookie support for restricted content.
- 7 fixes, all configurable via environment variables.

### Guided onboarding
The human's insight: *"A new user runs one command and has no idea what to do next."*

This led to:
- Next-step hints after every command (to stderr, never stdout).
- `--quiet`/`-q` flag for scripting (power users).
- `quickstart` subcommand: a concrete, copy-pasteable walkthrough using @TED.
- First-run detection: empty DB triggers a "try quickstart" suggestion.

**Result:** 138 tests passing. The tool became self-teaching.

**Key insight:** The `--quiet` flag was non-negotiable. Every hint system needs an escape hatch. Without it, the tool would annoy the exact power users who are its most valuable advocates.

---

## Session 5: Background Jobs

**Focus:** Non-blocking long-running operations.

**The user's request:** *"If someone issues a long running bulk command, there should be a suggestion to run it in background with estimated time. Also there should be an option to bring it to foreground and watch how it's going. But if it is pushed to background other tasks the user issues should not get blocked by it."*

**What we built:**
- `--bg` flag: pushes any bulk operation to a detached background process.
- `yt-artist jobs`: list all jobs with progress (done/total).
- `jobs attach <id>`: tail the log file in real time.
- `jobs stop <id>`: send SIGTERM for graceful shutdown.
- `jobs clean`: remove old finished jobs.
- Time estimation: before bulk operations with 5+ videos, show estimated duration and suggest `--bg`.
- Crash safety: stale PID detection, SIGTERM handler, try/except wrapping.

**Technical decision:** OS-level process detachment (`subprocess.Popen(start_new_session=True)`) over task queues (Celery/Redis), threading (can't survive terminal close), or multiprocessing (fork + SQLite = fragile). Zero new dependencies.

**Result:** 170 tests passing. Users can fire-and-forget long operations.

---

## What We Built: By the Numbers

| Metric | Value |
|--------|-------|
| Source files | 12 Python modules |
| Source lines | 2,775 |
| Test files | 20 test modules |
| Test lines | 2,852 |
| Total tests | 170 |
| ADRs | 11 (0001-0011) |
| New modules created | `jobs.py` (381 lines) |
| Sessions | 5 |
| Test growth | 81 → 99 → 109 → 138 → 170 |

---

## What the Human Taught the AI

1. **"What would frustrate a user?"** — The single most impactful reframe. Technical correctness is necessary but not sufficient. User experience is the product.

2. **Conservative defaults, aggressive configurability.** — Don't make users read docs to be safe. Don't make power users read code to go fast.

3. **Every hint needs an off switch.** — `--quiet` was insisted upon before a single hint was written. The human understood that helpful-by-default becomes annoying-always without an escape hatch.

4. **Ship incrementally, test always.** — Every session ended with all tests green. No "we'll fix the tests later." The human pushed for this discipline and it prevented regression cascades.

5. **Think in workflows, not features.** — The `quickstart` command and next-step hints weren't features; they were a workflow made visible. The human consistently thought about the end-to-end user journey.

---

## What the AI Learned About Working Together

1. **Plan before coding.** — The plan-then-implement pattern (used formally from Session 5 onward) produced better architecture and fewer rewrites. When the human approved the plan, implementation was faster because scope was locked.

2. **Show your reasoning.** — The human valued seeing alternative approaches and trade-offs (e.g., "Celery vs subprocess vs threading" in the background jobs plan). Transparency builds trust.

3. **Test-first isn't just quality; it's communication.** — Test names like `test_quiet_suppresses_hints` communicate intent better than comments. They became a shared language for "what this feature does."

4. **Match the codebase's style.** — Every new file followed existing patterns (same import style, same test helper patterns, same docstring format). Consistency signals care.

5. **Respect the parking lot.** — When the human said "Everything below goes to a parking lot for now," that was a scope boundary. Respecting it meant finishing the current work properly instead of spreading thin.

---

## How to Improve Future Sessions

### For the human
- **Start with the user story.** Your best prompts were user-focused: "What would frustrate someone?" and "There should be a way to bring it to foreground." These gave clearer direction than technical specifications.
- **Keep asking "what else?"** after each feature. Your iterative deepening (audit → fix → audit again) found issues that a single pass would miss.
- **Consider writing acceptance criteria up front.** For the background jobs feature, the natural-language description was excellent; for future complex features, a short list of "must pass" scenarios would speed up planning.

### For the AI
- **Propose the plan proactively.** Don't wait to be asked. When the task is non-trivial, immediately suggest entering plan mode.
- **Show test counts after every change.** The human cared deeply about test counts. Make "all N tests pass" the closing line of every implementation block.
- **Flag trade-offs explicitly.** When making a decision (e.g., "2 workers default"), state the trade-off ("safety vs speed") and let the human override.

### For both
- **Always ask: what should we always ask?** This meta-question from the human was itself the most useful question. Keep asking it.

---

## The Road Ahead (Parking Lot)

Items explicitly deferred for future sessions:

1. **Performance optimization:** Analyze which modules could benefit from Rust/Go/C++ for hot paths (yt-dlp subprocess management, large transcript processing).
2. **Licensing and payment:** Design a freemium model (e.g., free for 10K summaries, paid tiers for 50K+). What usage metrics do users need to see?
3. **Usage metrics tracking:** What counters matter? Summaries generated, transcripts processed, API calls made, processing time.
4. **Shippable product:** Package for distribution (PyPI, Homebrew tap). Onboarding flow for non-technical users. Documentation polish.

---

*This document was written as part of the yt-artist project to record the collaborative development process and the lessons learned from building software through human-AI partnership.*
