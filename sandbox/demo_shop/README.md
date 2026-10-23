# Demo Shop service

A tiny pricing service used to demonstrate Jarvis's agentic tasks.

## Deployment checklist

A release is ready for deployment when:

1. The unit tests pass: `python -m unittest` (run in this folder).
2. The release build succeeds: `python build.py`. It writes `release/manifest.json`.
3. The release version is **1.0.0**, recorded in a `VERSION` file in this folder.

## Layout

- `shop/pricing.py` - price calculations (discounts, tax).
- `tests/test_pricing.py` - unit tests.
- `build.py` - builds the release manifest.
