# DjangoProto8 CI Notes

This project is a Django-rendered UI using templates and static assets. A Node frontend is not required by default.

`scripts/ci.sh` and `make ci` run the backend pipeline (`pip install`, Django `check`, `collectstatic`, and tests) as gating steps.

Node steps are conditional. If `package.json` is missing, CI prints `No package.json detected; skipping Node pipeline.` and continues.

Lint and format checks (`black --check`, `pylint`) are advisory for now. Their results are reported, but they do not fail CI until the baseline is normalized.
