# Round 4 fourth-repair PoC set

These scripts reproduce the observations in `../ASSESSMENT-round4.md`
(2026-11-15 review of the `ae718c4` fourth-repair materials sent with
`ROUND3-INVITATION.md` / `ROUND3-FIX-RESPONSE.md`). The samples are authorized
security-test artifacts only; do not redistribute them.

Environment used for the recorded logs:

- Windows 10 Pro 10.0.19045 x64
- CPython 3.12.0
- Django 5.2.8
- PyInstaller 6.22.3
- cryptography (for the challenge's chacha build)

## Extract

```powershell
Expand-Archive review\round3\round3-django-shielded.zip -DestinationPath review\round3\extracted
Expand-Archive review\round3\PyShieldBuilder-protected-sample-round3.zip -DestinationPath review\round3\extracted
```

The challenge tree is `review/round3/extracted/round3-django-shielded`; the
samples are `review/round3/extracted/selfbuild-protected-sample`.

## New fourth-round PoCs

```powershell
# A tampered __pycache__ entry executes while the signed manifest passes.
python review\round3\poc-round4\pycache_tamper.py review\round3\extracted\round3-django-shielded

# check_release.py false negatives: master/build key in a pyshield* TEMP entry
# and in a zip without a sibling *.shield-secret.json are not detected.
$env:PYSHELD_REPO = (Get-Location).Path
python review\round3\poc-round4\check_release_gap.py review\round3\extracted\selfbuild-protected-sample

# The checker itself returns OK on the current challenge zip even though it
# contains demo/__pycache__ entries.
python tools\check_release.py review\round3\round3-django-shielded.zip
python tools\check_release.py --temp
```

## Existing behavior tests

The generic scripts copied from `poc-fixed` take a tree and a build-key hex
argument. Derive the build key from the tree's keyring; do not put production
keys in this directory.

```powershell
python review\round3\poc-round4\recover_flag.py <challenge-tree>
python review\round3\poc-round4\keyring_timing.py <challenge-tree> 5
python review\round3\poc-round4\binding_concurrency.py <sample-tree> <build-key-hex> 16 10
python review\round3\poc-round4\firstrun_build.py
python review\round3\poc-round4\firstrun_concurrency.py
python review\round3\poc-round4\clock_concurrency.py <tree> <build-key-hex> 16 5
python review\round3\poc-round4\startup_concurrency.py <challenge-tree> 16 3
python review\round3\poc-round4\legacy_state_tests.py <sample-tree> <build-key-hex>
python review\round3\poc-round4\binding_guard_tests.py <sample-tree> <build-key-hex>
python review\round3\poc-round4\exe_extract.py <exe> main|selfbuild
python review\round3\poc-round4\sample_audit.py <sample-tree>
```

`firstrun_build.py` writes `run/firstrun/dist.shield-secret.json` under
`poc-round4/`; that file contains build-key material and must not be committed
or published. The recorded logs were produced under
`E:\tmp\workbuild\code\分析\round4_new`; paths in the logs are evidence paths.

## Logs

The logs cited by the report are in `logs/`:

- `recover-flag.log`, `keyring-timing.log`, `artifact-scan.log`
- `challenge-check.log`, `challenge-reveal.log`, `challenge-verify.log`,
  `hidden-import.log`
- `logs-binding-direct.log`, `logs-firstrun-concurrency.log`,
  `logs-clock-sample.log`, `logs-clock-challenge.log`, `logs-startup.log`,
  `logs-clock-failure.log`, `logs-legacy-state.log`, `legacy-midprocess.log`,
  `logs-binding-guard.log`
- `pycache-tamper.log`, `check-release-gap.log`,
  `check-release-challenge.log`, `check-release-temp.log`
- `exe-selfbuild.log`, `exe-main.log`, `sample-audit.log`, `tests.log`,
  `ci-runs.json`, `challenge-zip-list.log`

`logs/tests.log` is the full 180-test run (1 skipped native toolchain test).
