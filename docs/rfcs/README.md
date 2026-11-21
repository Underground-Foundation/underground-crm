# Requests for Comments (RFCs)

This directory holds architecture and integration proposals for Underground CRM.
There was no formal RFC convention in the repository before RFC 0001, so this
README establishes a deliberately light one.

## What an RFC is

An RFC is a **discussion document**, not an implementation plan and not a decision.
It should:

- state the problem before proposing a solution;
- distinguish observations, evidence, assumptions, and recommendations;
- cite repository files and upstream documentation;
- describe alternatives, including doing nothing;
- invite maintainer feedback;
- avoid modifying application code, schemas, or deployment configuration.

Implementation work happens in separate pull requests after an RFC has been
discussed. An RFC may recommend *not* implementing something.

## Naming and status

- Files are numbered and named `NNNN-short-title.md`.
- The status is one of: `Draft`, `Discussion`, `Accepted`, `Rejected`,
  `Superseded`, or `Withdrawn`.
- A Draft may be opened as a draft pull request for comment. It should not be
  merged as an implementation commitment.
- If a later RFC replaces an earlier one, link both documents.

## Suggested structure

1. Executive summary
2. Current architecture and constraints
3. Problem statement
4. Research methodology and sources
5. Comparative findings
6. Proposed improvements
7. Alternatives considered
8. Compatibility, privacy, and security implications
9. Incremental implementation options
10. Open questions for maintainers
11. Recommended next steps

## Current RFCs

| RFC | Title | Status |
|---|---|---|
| [0001](0001-identity-membership-and-integration-architecture.md) | Identity, Membership, and Integration Architecture | Draft |
