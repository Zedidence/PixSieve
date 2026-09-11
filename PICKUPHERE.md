# Pickup Notes — PixSieve Consolidation Work

Last worked on: 2026-07-06. Nothing from this session is committed to git yet.

## Where things stand

This session ported two standalone tools into PixSieve so there's one maintained image tool
instead of three: `faveRemover` (done, deleted) and `ImageGalleryEditor` (code done, **manual
browser verification still needed before deleting the source folder**).

Full plan/history: `C:\Users\zdaly\.claude\plans\serialized-launching-lighthouse.md`

## What's done

- **Part 0 — dev-server staleness fix** (`pixsieve/app.py`): `TEMPLATES_AUTO_RELOAD=True`,
  `SEND_FILE_MAX_AGE_DEFAULT=0`. This was the root cause of "I don't see the new feature" earlier
  in the session — template/static caching was hiding live edits. **Restart the server once** to
  pick this up if you haven't already; after that, template/JS/CSS edits should always show up on
  a normal refresh.
- **Part 1 — faveRemover → PixSieve**: new `strip_favorite_ratings` operation, wired into both the
  CLI (`strip-ratings` subcommand) and the web UI (METADATA sidebar). Tested. `faveRemover/` folder
  deleted.
- **Part 2 Phase 1 — rotate/flip/save** in the lightbox: `POST /api/image/save`, `editor.js`,
  toolbar UI. Live.
- **Part 2 Phase 2 — crop, undo/redo, rename, delete, metadata info bar**: full parity with
  ImageGalleryEditor's feature set (minus gallery browsing, which PixSieve's own duplicate-groups
  view already supersedes). New backend route `POST /api/image/rename`. All of this went through a
  4-dimension adversarial code review afterward, which found and got 6 real bugs fixed:
  - Delete/rename requests left a race window open — a slow response arriving after the user
    canceled and navigated elsewhere could corrupt an unrelated image's editor state or slam the
    lightbox shut. Fixed with in-flight guards that block modal dismissal until the request
    resolves.
  - A failed single-image delete showed "Unknown error" instead of the real server message.
  - Double-clicking crop's Apply button (or double-pressing Enter) could apply the same crop twice.
  - A rename to a name with a trailing dot/space would succeed on Windows under a different actual
    filename than the one reported back.
  - Escape, after Tabbing to a modal's Cancel button, closed the whole lightbox instead of just the
    modal.

Test suite: 233 passed, 2 skipped. (See "Known pre-existing issues" below for the unrelated
failures — not caused by this work.)

## What's NOT done yet

1. **Manual in-browser verification** — I don't have a browser in this environment, so none of the
   crop/undo/rename/delete UI has actually been clicked through by a human yet. Checklist below.
2. **Delete `ImageGalleryEditor/`** — intentionally held until #1 is confirmed, since it's the only
   remaining reference copy of the original behavior.
3. **Commit the work** — nothing from this session is committed. See the git note below before you
   commit anything.

## Manual verification checklist (do this first)

1. Fully restart the PixSieve server process, then hard-refresh the browser once (Ctrl+Shift+R) to
   clear anything cached from before the Part 0 fix.
2. Scan a folder with a jpg (ideally one with known EXIF rotation), a png, and a gif.
3. Open the jpg in the lightbox — confirm the edit toolbar (rotate/flip/crop/undo/redo/discard/save)
   appears; open the gif — confirm it's hidden (by design, editing is jpg/jpeg/png/webp only).
4. Crop the jpg — check the live dimension label, the hole-punch overlay (not an opaque box), and
   rule-of-thirds guides. Apply, then Save, then reload fresh and confirm the on-disk dimensions
   match.
5. Rotate → Flip → Crop in sequence, then Undo three times / Redo three times — confirm each step
   is exactly one operation, and a new edit after an Undo clears the Redo stack.
6. Press F2, try renaming to an existing name (expect a 409, no change), then to a valid new name —
   confirm the lightbox, thumbnail grid, and any prior keep/delete mark follow the renamed file.
7. Try F2 while an edit is pending and unsaved — confirm it's blocked with a toast.
8. Press Delete on the second-to-last image in a duplicate group — group should disappear and the
   lightbox should close. Press Delete in a group with ≥3 images — lightbox should advance and stay
   open, and the modal should be fully visible/clickable on top of the lightbox.
9. Specifically re-try the two race conditions that were just fixed: start a delete or rename, then
   immediately try to Cancel and navigate away before the request would normally finish — confirm
   the modal now stays locked/disabled until the response comes back, rather than letting you
   navigate away mid-request.
10. Confirm the filename/dimensions/size info bar is present and updates after a crop and after a
    rename.

If all of that looks right, delete `ImageGalleryEditor/` and this port is complete.

## Known pre-existing issues (not caused by this session's work)

- `tests/test_operations_metadata.py` fails to collect, and 4 tests in
  `TestApiMetadata::test_randomize_exif_*` plus `test_operations_pipeline.py::TestAvailableSteps::test_all_steps_defined`
  fail. Root cause: an already-uncommitted refactor (present before this session started) renamed
  `randomize_exif_dates`/`randomize-exif` into `randomize_dates`/`randomize-dates` with a `sync_exif`
  flag, but never updated the tests referencing the old names. Confirmed via `git diff` this
  predates everything done today. Not fixed — out of scope for the consolidation work, but worth
  cleaning up separately. (Fixed 2026-09-10: updated the stale tests to reference randomize_dates/
  randomize-dates instead of randomize_exif/randomize-exif.)
- There's a large pile of **pre-existing, unrelated uncommitted changes** already sitting in this
  repo from before this session (`git status` shows modifications to `README.md`, `docs/api.md`,
  `docs/changelog.md`, `pixsieve/api/orchestrator.py`, `pixsieve/operations/metadata.py`,
  `pixsieve/operations/pipeline.py`, `pyproject.toml`, `requirements.txt`, `setup.py`,
  `tests/README.md`, plus untracked `Dockerfile`, `docker-compose.yml`, `docs/modernization.md`,
  `tests/e2e/`, `pixsieve/static/js/filter-worker.js`). None of that was touched by this session —
  it looks like in-progress work from before. **Worth sorting out before committing** — you'll want
  to decide whether to commit this session's changes separately from that older pile, or review
  what that pile actually is first.

## Files touched this session (for reference)

New: `pixsieve/operations/ratings.py`, `pixsieve/api/schemas.py`, `pixsieve/static/js/editor.js`,
`pixsieve/static/sw.js`, `tests/test_operations_ratings.py`.

Modified: `pixsieve/app.py`, `pixsieve/config.py`, `pixsieve/operations/__init__.py`,
`pixsieve/utils/platform.py`, `pixsieve/utils/__init__.py`, `pixsieve/cli/arg_parser.py`,
`pixsieve/cli/orchestrator.py`, `pixsieve/cli/operations_orchestrator.py`,
`pixsieve/api/operations_routes.py`, `pixsieve/api/routes.py`, `pixsieve/templates/index.html`,
`pixsieve/static/css/app.css`, `pixsieve/static/js/app.js`, `tests/test_cli_operations.py`,
`tests/test_api_operations.py`.

Deleted: `faveRemover/` (top-level, outside this repo).
