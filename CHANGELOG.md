# Changelog

## 0.1.1

- Publish the requested climate mode immediately in Home Assistant while the
  remote operation is running.
- Revert an optimistic mode on a confirmed command failure; keep an unknown
  result temporarily while checking cached cloud state.
- Wait for the vehicle status refresh to finish before sending climate START,
  up to the server-provided wake-up duration.
- Distinguish explicit server rejection from a genuinely unknown network
  outcome while preserving the no-retry rule for START and STOP.

## 0.1.0

- Initial Japanese Mitsubishi Motors integration with authentication, rotating
  refresh tokens, cached status polling, and verified 25 °C climate start/stop.

