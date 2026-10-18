# Development and testing

[Back to README](../README.md)

See [Project direction and architecture](ARCHITECTURE.md) for the AI website
interaction scope, shared infrastructure, and integration design guidelines.

Run commands below from the repository root with Python 3.14 or newer and Git
installed. `uv sync` installs the project and development dependency group
from `uv.lock`.

```bash
uv sync
uv run python -m playwright install chromium
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check --error all
```

Local browser fixtures use Playwright Chromium; the opt-in live suite and
sign-in helper use CloakBrowser's separately downloaded Chromium. These are
different browser installations. Local tests do not establish that current
signed-in websites or account-specific options work.

## Local test coverage

| Tests | Coverage |
| --- | --- |
| `test_settings.py`, `test_jobs.py`, `test_polling.py`, `test_lifecycle.py` | Flow settings read-back, job attribution/polling, and lifecycle failure handling |
| `test_selectors.py`, `test_manifest_flows.py` | Local browser fixtures for selectors and YAML download/settings workflows |
| `test_page_activity.py` | Page activity setup/failure cleanup and background rendering with Chromium |
| `test_gemini.py`, `test_gemini_lifecycle.py` | Local Gemini DOM fixtures: models, thinking levels, response attribution/completion, recovery, Temporary mode, and deletion |
| `test_video.py` | FFmpeg assembly, output preservation, concurrent temporary files, and cancellation |

Tests requiring FFmpeg/FFprobe are skipped if those tools are absent. For a
focused Gemini check:

```bash
uv run pytest tests/test_gemini.py tests/test_gemini_lifecycle.py
```

On Windows environments that deny access to the default uv cache or pytest
temporary directory, use workspace-local paths in PowerShell:

```powershell
New-Item -ItemType Directory -Force output | Out-Null
$env:UV_CACHE_DIR = Join-Path (Get-Location) "output/uv-cache"
uv run pytest -p no:cacheprovider --basetemp=output/pytest-docs
```

Pytest clears the supplied `--basetemp` directory; reserve that path for test
scratch files. `output/` is ignored by Git.

## Sign-in and profiles

Open a headed browser, sign in, wait for the site workspace, and close the
browser window before running tests:

```bash
uv run python -m tests.live.sign_in
uv run python -m tests.live.sign_in --site gemini
# An explicit profile path overrides the site's default:
uv run python -m tests.live.sign_in ./custom-gemini-profile --site gemini
```

| Site | Pytest option | Environment variable | Default directory |
| --- | --- | --- | --- |
| Flow | `--flow-profile PATH` | `GOOGLE_FLOW_PROFILE` | `./google-profile` |
| Gemini | `--gemini-profile PATH` | `GOOGLE_GEMINI_PROFILE` | `./gemini-profile` |

The sign-in helper uses the same environment defaults, with an optional
positional profile path. Keep custom profiles out of version control as well;
they contain cookies and sessions. The default directories are already ignored.

A profile can be used by only one browser process at a time. Close the sign-in
browser and avoid simultaneous pytest runs on one profile. Use distinct Flow
and Gemini profiles when running the full live suite, since Flow's browser
context remains open for the test session. A focused Gemini run can use an
existing signed-in Google profile via `--gemini-profile google-profile` after
any browser using that profile has closed.

Signed-out Flow profiles fail the initial workspace check. Gemini reports
`GeminiAuthenticationError` for sign-in and `GeminiOpenError` when Google
requires an unusual-traffic check. Complete that check in the headed profile
before retrying. The tests use headed browsers by default; `--headless` is
available, but Google may challenge those sessions.

## Opt-in live tests

| Invocation | Enabled tests and account effects |
| --- | --- |
| `uv run pytest` | Local tests; real-site tests are skipped |
| `uv run pytest --live` | Local and real-site tests except Flow credit tests; includes Gemini text quota use and disposable-chat deletion |
| `uv run pytest --live-credits` | Implies `--live` and also enables Flow generation-credit tests |

Use focused commands when only one provider or behavior needs checking:

```bash
# Flow project, media, settings, and parallel-tab checks without generation:
uv run pytest tests/live --ignore=tests/live/test_gemini.py --live
# Flow generation tests, including image/video downloads and job attribution:
uv run pytest tests/live/test_generation.py --live-credits
# Gemini conversations, settings, Temporary mode, and disposable-chat deletion:
uv run pytest tests/live/test_gemini.py --live
# Two- and four-page Flow activity checks without generation:
uv run pytest tests/live/test_parallel_pages.py --live
```

Flow fixtures create throwaway projects and attempt to delete them afterward;
cleanup failures emit a warning with the URL for manual removal. Generation
tests explicitly select `Nano Banana 2.1` for images and `Veo 3.1 - Fast` for
videos. Account availability may differ, so inspect test constants before a
credit-spending run.

Gemini tests discover the account's model labels and verify settings through
read-back. The conversation test sends two prompts and leaves the saved chat
in history. The thinking-level test selects the offered levels for each model
and restores original settings. Lifecycle tests send a prompt in native
Temporary mode and permanently delete one disposable saved chat created by
that exact invocation; they never open an existing chat as a deletion target.
Gemini prompts consume text quota without needing `--live-credits`.

Record local, live, and credit-spending results separately. Passing fixture
tests does not prove live-site compatibility; a live run blocked by sign-in or
browser verification does not prove a provider regression. Gemini's recorded
DOM observations and historical validation boundaries are in
[its guide](GEMINI.md#recorded-validation).

## Adding a site

Shared infrastructure lives in `src/generative_ai/site/`. Gemini and Flow
provide examples of text-chat and stateful media facades respectively. Design
new integrations around the website's own interactions and result types; reuse
the shared lifecycle and YAML workflow infrastructure.

1. Add `src/generative_ai/foo/manifests/selectors.yaml` and
   `manifests/actions/*.yaml` for selectors and linear `web_page` UI flows.
   Keep stateful attribution, branching, and deadline handling in Python.
2. Register a `Site` in `foo/constants.py`:

   ```python
   from pathlib import Path

   from generative_ai.site import Site, register_site

   SITE = register_site(
       Site(
           name="foo",
           base_url="https://foo.example/",
           manifests_dir=Path(__file__).parent / "manifests",
       )
   )
   ```

   `Site.attach(page)` sets default timeouts and loads selectors/actions.
   It locks the viewport only if the site configures one. Use `keep_active=True`
   for Chromium sites requiring focus emulation; the context must then support
   `new_cdp_session`. Attach before navigation. `get_site("foo")` finds a
   registered site by name.
3. Derive provider errors from `SiteError` and the facade from `SitePage`.
   Implement `_closed_error`, decorate public async operations with
   `@page_required`, and construct the facade using `await SITE.attach(page)`.
   The decorator requires a live tab and serializes operations per facade.
4. Clean up owned tabs on constructor failure/cancellation using
   `close_page_and_report_failure` and `note_cleanup_failure`. Preserve the
   caller's context. Verify UI mutations and report uncertain remote state
   after an ambiguous destructive confirmation.
5. Re-export public names from `foo/__init__.py`, add local DOM/state tests,
   and keep real-site tests behind the existing opt-in markers. Document
   account effects and any unverified live behavior.
