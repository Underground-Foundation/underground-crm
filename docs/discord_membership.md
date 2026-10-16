## Optional Discord membership lookup

This generic, read-only integration is disabled by default. A downstream bot owns
its organization's server and role policy. No Discord credentials belong in the CRM.

Configure `DISCORD_MEMBERSHIP_INTEGRATION_TOKEN` with a dedicated high-entropy random
secret of at least 32 characters and `DISCORD_MEMBERSHIP_TYPE_ID` with the UUID of
the eligible MembershipType. Missing or invalid configuration, including a configured
membership type that no longer exists, returns HTTP 503. Rotate the shared secret
in both services together. Use HTTPS and restrict network access to the bot where practical;
the token permits eligibility queries for arbitrary Discord IDs.

The bot sends `GET /api/integrations/discord/membership/<discord_id>/` with
`Authorization: Bearer <secret>`. HTTP 200 contains only `{"eligible": true}` or
`{"eligible": false}`. An allauth SocialAccount must link that Discord UID to a CRM
Person with a membership of the configured type: its start is no later than now,
it has no suspension date, and its expiry is absent or no earlier than today in the
CRM timezone. Unknown and unlinked IDs produce the same false response. The endpoint
never exposes names, email addresses, membership records, or login redirects.

Responses prohibit caching. HTTP 401 means a missing or incorrect integration token;
400 means an invalid ID; 405 means an unsupported method. A bot must treat HTTP
errors as unavailable verification, not a negative membership decision, and retry
later without changing existing roles.

A downstream bot can check on server join, an explicit verification command, and a
periodic reconciliation. Successful CRM login/linking takes effect on its next poll
(or immediate verification command); there is deliberately no CRM-to-bot callback.
Disconnecting Discord, expiry, suspension, and cancellation also take effect on the
next reconciliation. The downstream deployment chooses and documents that interval.
All tests use synthetic identities; deployment and live membership access are separate.
