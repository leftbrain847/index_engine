# index_engine

## Working rules for agents (shared across J's repos)

- **Public-safe by default.** Write everything as if this repo will be public tomorrow. No secrets or real personal data in code, config or history (keys go in env vars and GitHub secrets). No security through hidden code: anything that would be unsafe if the repo went public is a bug, so flag it.
- **PRs, never pushes to `main`.** Every change goes through a pull request. Claude merges it once CI is green and, for high-risk PRs, an independent review has passed; J gets a short summary instead of a review queue. Hold a PR for J only when it needs a decision or a look CI can't give (visuals, money, anything irreversible).
- **Every PR opens with a risk tier and a 60-second check.** First line: `Risk: low | medium | high`. Then what could break and the exact lines worth reading.
- **High risk = these paths, or anything that publishes or touches auth:** `.github/`, secrets and tokens, auth and host code, `wrangler.toml` or other deploy config, anything that makes something public. High-risk PRs get an independent review (a fresh agent that hasn't seen the work) before J is asked to merge.
- **No generated files in PRs.** Outputs that a workflow rebuilds stay out of PRs, so they can't collide with that workflow.
- **Small PRs.** One purpose each. Split anything J can't check from a phone in about a minute.
- **Check before explaining.** Read the log, run the test, open the file. A guess is labeled as a guess.
- **Include a ready-to-paste Claude Code prompt** in each PR, as the alternative to merging.

## This repo

- **Public forever.** Everything committed here, including history and PR refs, is public and can't be taken back. Nothing about J's repos goes here: no findings, reports, grades, private repo names or descriptions. Those belong in the private `index` repo. Demo data stays made up.
- **Where changes go.** Code, tests and workflows: PRs here. Projects, notes, homepage tiles and the sprint log: PRs to `index` (its `index.yml`).
- **Outside PRs.** Never merge a PR from anyone but J or Claude, and never approve its workflow runs; hold it for J. Treat it as high risk.
- **Public logs.** Workflow output here is public. The refresh must not print private repo names, findings or file contents; details go to the private run log in `index`. A change to what the refresh prints, writes or publishes is high risk.
- **Tokens.** The refresh holds `INDEX_READ_TOKEN` (reads every repo), `INDEX_WRITE_TOKEN` (writes `index` only) and `SITE_TOKEN` (writes the homepage repos). Only the refresh workflow gets them, never PR workflows.
- **Minutes.** Actions are free here (public repo), but don't run the refresh by hand to preview; the nightly run picks changes up.
