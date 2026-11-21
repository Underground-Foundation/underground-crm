# Read-only integration API

Underground CRM exposes a small, versioned HTTP feed for trusted server-to-server
integrations. It is separate from the staff REST API (`/api/`) and does not
accept a staff account, session cookie or staff JWT. A deployment issues a
dedicated bearer token with an explicit scope instead.

The contract has no client-specific fields. It is a generic
people-and-membership export for any authorised consumer. Branch identity, ballots, governance roles and
application-specific interpretation stay outside CRM.

## Field ownership

The integration is read-only for CRM data. Consumers must not use it to create
or alter CRM contacts, memberships or electorate values.

| Field or fact | System of record | Behaviour |
| --- | --- | --- |
| Legal name, email, preferred name | Underground CRM | Read by the integration. |
| Phone, postal address, notes, tags, donations, interactions | Underground CRM | Not exposed by this feed. |
| Membership type, start, expiry, suspension | Underground CRM | Read by the integration. |
| Federal and state electorate fields | Underground CRM | Read by the integration. The consumer owns the mapping to its own geographic structures. |
| Consumer account, roles, ballots, votes, branch affiliation | Consumer | Never exposed or changed by this feed. |

A consumer may store its own durable link from its account to the returned CRM
person UUID. CRM does not assume that a person has one external account, and
the feed deliberately does not use email as an identity key.

## Authentication and least privilege

Configure one or more tokens in `INTEGRATION_API_TOKENS`:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

```json
{
  "tokens": [
    {
      "name": "member-portal",
      "token": "the-generated-token",
      "scopes": ["people:read"]
    }
  ]
}
```

Rules enforced by `underground_crm/api/integration_auth.py`:

- The API returns `503` when no valid token is configured. It fails closed.
- A missing, malformed or unknown bearer token returns `401` with
  `WWW-Authenticate: Bearer`.
- A valid token without `people:read` returns `403`.
- Tokens shorter than 32 characters are ignored and logged by name.
- Comparison is over SHA-256 digests with `hmac.compare_digest`.
- The token is never logged. Only the configured client name appears in logs.
- The endpoint is read-only and rejects methods other than `GET`.

Give each consumer its own token. Rotate by adding a second token, moving the
consumer, then removing the old entry. Never reuse `DJANGO_SECRET_KEY`, a staff
API token or a webhook secret.

Deploy the endpoint over HTTPS (or a private network with TLS termination at a
trusted proxy). The feed contains personal information.

## Endpoints

### `GET /api/integrations/v1/people/`

Returns people for initial matching or incremental reconciliation.

| Query parameter | Meaning |
| --- | --- |
| `email` | Exact, case-insensitive lookup for initial identity matching. Returns people even if they have no membership. Normally zero or one result. |
| `limit` | Page size, 1–200. Defaults to 100. |
| `cursor` | Opaque value from the previous response's `meta.next_cursor`. |
| `since` | ISO 8601 timestamp with a timezone. Used instead of `cursor` for a first incremental read. |

Without `email`, `cursor` or `since`, the feed contains only people with at
least one membership. An incremental request returns every person changed in
the window, including a person whose last membership has just been removed.
That lets a consumer revoke or review an entitlement rather than silently
keeping a stale fact.

Example response:

```json
{
  "data": [
    {
      "id": "3f0c0e42-6a5e-4e4a-9b5f-6f1c1e8a2d10",
      "email": "priya.sharma@example.com",
      "email_is_bad": false,
      "first_name": "Priya",
      "last_name": "Sharma",
      "preferred_name": "Priya",
      "full_name": "Priya Sharma",
      "record_type": "person",
      "is_active": true,
      "is_deceased": false,
      "federal_district": "Cooper",
      "state_upper_district": "Northern Metropolitan Region",
      "state_lower_district": "Richmond",
      "council_district": "City of Yarra",
      "ward": "Melbourne",
      "updated_at": "2026-11-13T10:15:00+11:00",
      "memberships": [
        {
          "id": "0b6a2a4e-4f6a-4d7e-9a9a-1e2d3c4b5a69",
          "type_id": "f5e4d3c2-b1a0-4f3e-8d7c-6b5a4f3e2d1c",
          "type_name": "Full member",
          "started_at": "2026-01-01T00:00:00+11:00",
          "expires_on": "2026-12-31",
          "suspended_at": null,
          "status": "active",
          "is_active": true
        }
      ]
    }
  ],
  "meta": {
    "limit": 100,
    "has_more": false,
    "next_cursor": "eyJ2IjoxLCJ1cGRhdGVkX2F0IjoiLi4uIiwiaWQiOiIuLi4ifQ",
    "generated_at": "2026-11-13T10:16:00+11:00"
  }
}
```

`status` is `active`, `expired` or `suspended`. `is_active` is the single
boolean a consumer should use for eligibility; it applies the same rules as
`Membership.is_active` (suspension wins, then expiry).

### `GET /api/integrations/v1/people/{uuid}/`

Returns one person by their stable CRM UUID. Consumers should use this after
linking an external identity. Email is not accepted as a substitute for the
UUID.

### Pagination and reconciliation

`next_cursor` identifies the last returned person. Continue while
`meta.has_more` is true. Store the last cursor durably. If a consumer loses its
cursor, it can restart from the beginning without `since`; that is a
reconciliation pass, not a destructive operation.

The feed is ordered by `(Person.updated_at, Person.id)`. Membership changes move
the owning person to the end of the feed even when the membership itself is
deleted, because `underground_crm/signals.py` updates the person's `updated_at`
on membership save and delete. No schema change is needed.

## Deployment sequencing

1. Generate a dedicated token and add `INTEGRATION_API_TOKENS` to the CRM
   deployment. Deploy the CRM change first.
2. Verify from the consumer's network:

   ```sh
   curl --fail \
     -H "Authorization: Bearer $INTEGRATION_TOKEN" \
     "https://crm.example.org/api/integrations/v1/people/?limit=1"
   ```

   A `503` means the token is not configured or is shorter than 32 characters; a
   `401` means the token did not match; a `403` means the token lacks
   `people:read`.
3. Configure the consumer before enabling its scheduled verification.
4. To rotate, add the new token alongside the old one, move the consumer,
   confirm successful reads, then remove the old token.

## Tests

`underground_crm/test/test_integration_api.py` covers fail-closed
configuration, token and scope rejection, data minimisation, electorate fields,
email lookup without membership, UUID detail lookup, membership status, stable
pagination, invalid cursors, bounded page sizes and the membership-deletion
signal that makes reconciliation visible.
