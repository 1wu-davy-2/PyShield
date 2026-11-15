# Round 3 fixed-materials PoC set

These scripts reproduce the observations in `../ASSESSMENT-round3-fixed.md`
(2026-11-15 re-review of `develop @ 8adeccf`). The samples are authorized
security-test artifacts only; do not redistribute them.

Environment used for the recorded logs:

- Windows 10 Pro 10.0.19045 x64
- CPython 3.12.0
- Django 5.2.8 (for the challenge app)
- PyInstaller 6.22.3 (for the offline PYZ extraction)
- cryptography (for the challenge's chacha build)

## Extracting the materials

```powershell
Expand-Archive review\round3\round3-django-shielded.zip -DestinationPath review\round3\extracted
Expand-Archive review\round3\PyShieldBuilder-protected-sample-round3.zip -DestinationPath review\round3\extracted
```

The scripts that only need a tree take its path as the first argument:

```text
python recover_flag.py  <challenge-tree>
python keyring_timing.py <challenge-tree> 5
python sample_audit.py   <sample-tree>
```

`recover_flag.py` is the shortest reproducible path from the release directory
to the flag: it reads `_shield_runtime/_keyring.pyc`, executes the marshalled
code, calls `master_key()`, derives the build key with HKDF, and decrypts
`demo/hidden_flag.py`. It does not read the license and does not execute the
protected module.

Expected result:

```text
PSH-ROUND3-e3b2caa377c88630
SHA-256 a036bf169f93cf499e7496082e3d088e5f3c453b3620cf8eed00941b2d96c4ce
```

## State / binding tests

The concurrency and state scripts take a tree and a build-key hex string. Derive
the build key from the tree's keyring (the challenge build's build-key SHA-256 is
in the report); do not copy production keys into this directory.

```text
python clock_concurrency.py   <tree> <build-key-hex> 16 5
python binding_concurrency.py <tree> <build-key-hex> 16 5
python startup_concurrency.py <challenge-tree> 16 3
python clock_failure_tests.py <sample-tree> <build-key-hex>
python legacy_state_tests.py  <sample-tree> <build-key-hex>
python binding_guard_tests.py <sample-tree> <build-key-hex>
```

`clock_concurrency.py` / `binding_concurrency.py` create their working state
under `poc-fixed/run/`; they are safe to delete.

`firstrun_build.py` creates a small first-run-bound project under `run/firstrun`.
It writes `run/firstrun/dist.shield-secret.json`; that file contains build-key
material and must not be committed or published. `firstrun_concurrency.py` then
starts 16 copies of that project's `manage.py`.

## Exe / artifact inspection

```text
python exe_extract.py PyShieldBuilder.exe main
python exe_extract.py dist-selfbuild\PyShieldBuilder-selfbuild.exe selfbuild
python artifact_scan.py
python temp_hygiene.py
```

`exe_extract.py` imports the repository's unprotected `pyshield.crypto` helper
and PyInstaller's readers. It does not execute the exe.

`artifact_scan.py` expects the two extracted trees by default under
`review/round3/extracted/`; override with `PYSHELD_CHALLENGE_TREE`,
`PYSHELD_SAMPLE_TREE`, or `PYSHELD_ROUND_DIR`.

## Recorded logs

`logs/` contains the raw outputs cited in the report. `logs/tests.log` is the
full unit-test run (178 passed, 1 skipped; the skip is the native toolchain test).
`logs/license-v1-metadata.log` records that the challenge license is format 3,
has no `build_key`, and rejects the v1 salt. `logs/legacy-midprocess.log`
records the in-process legacy reset test.
