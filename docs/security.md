# Security and reliability

| Concern | Implementation |
|---|---|
| Secrets | Server-side only, via `TRACEMIND_*` env vars. `.env` is git-ignored. Startup refuses the development secret when `TRACEMIND_ENV=production` |
| Authentication | PBKDF2-SHA256 (310k iterations, per-user salt). HS256 JWT with expiry, in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` when configured), or a bearer header |
| Authorization | `require_project` / `require_incident` on every project-scoped route. Roles: owner > editor > analyst > viewer. Non-members get 404 for projects, incidents, runs, reports, events and ingestion jobs. Tested in `tests/test_api.py` |
| Public demo | The demo user is an *analyst* on the synthetic project only. No uploads, no settings or membership changes. The demo account's own e-mail is shown as "demo visitor" in member lists. At most 3 sandbox projects per demo session. Demo login is rate-limited |
| Upload validation | Extension allow-list, 25 MB limit (checked twice: `Content-Length` middleware and a bounded read), binary sniffing, UTF-8 check, 250k-row cap, 64 KB per-row cap, 16 KB attribute cap, strict ID and service-name patterns, numeric and range checks |
| Request limits | Global body-size guard. 30 s request timeout (uploads and SSE excluded) |
| Rate limits | Analysis 6/min/user, assistant 20/min/user, evaluation runs, login/register/demo per IP. Each returns 429 with `Retry-After` |
| Sanitisation | Control characters stripped. Markdown reports escape telemetry text. React renders all text escaped (no `dangerouslySetInnerHTML`). The `q` filters strip `LIKE` wildcards; queries go through SQLAlchemy only |
| Redaction | At ingestion: values of sensitive keys (password, token, secret, api_key, authorization, cookie, session, ssn, card) and bearer tokens, `key=value` secrets, card-like numbers and e-mail addresses in messages |
| Prompt injection | Telemetry is passed as aliased, truncated data. Instruction-like messages are flagged, both to the model and in the UI. The system prompt forbids following embedded instructions. The model has no tools. Output is schema-validated, citations are checked, and hypotheses about unknown services are dropped |
| Remediation | Suggestions are read-only text. Suggestions that would change a system are labelled "[Requires human approval]". Nothing is ever executed |
| Errors | Uniform error envelope with `request_id`. Unhandled exceptions are logged with stack traces but return a generic message |
| Logging | JSON structured logs: access logs with method, path, status, duration, user and request ID; job lifecycle logs |
| Headers | `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `X-Frame-Options: DENY` on the web app |
| Containers | Non-root users in both images. Health checks. Ports bound to 127.0.0.1 in Compose |

## Not done (recommended before real production data)

- CSRF tokens. `SameSite=Lax` plus JSON-only mutation endpoints mitigate most cross-site requests, but a double-submit token would be defence in depth.
- A distributed rate limiter and job queue for multi-instance deployments.
- Account lockout, e-mail verification and password reset.
- Retention policies and encryption at rest for object storage (depends on the provider).
