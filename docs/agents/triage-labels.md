# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| --------------------------- | --------------------- | ----------------------------------------- |
| `needs-triage`               | `needs-triage`         | Maintainer needs to evaluate this issue   |
| `needs-info`                 | `needs-info`           | Waiting on reporter for more information  |
| `ready-for-agent`            | `ready-for-agent`      | Fully specified, ready for an AFK agent   |
| `ready-for-human`            | `ready-for-human`      | Requires human implementation             |
| `wontfix`                    | `wontfix`              | Will not be actioned                      |

These five labels define workflow state. Keep orthogonal concerns in separate labels so an issue can be filtered by state, type, priority, and area independently.

## Additional label conventions

### Type

- `bug` — incorrect or broken behavior
- `feature` — new user-visible capability
- `docs` — documentation-only work
- `refactor` — internal restructuring without intended behavior change

### Priority

- `priority-high` — important or time-sensitive
- `priority-medium` — normal planned work
- `priority-low` — useful but deferrable

### Area

- `extension` — browser extension code
- `companion` — local Python service
- `ui` — overlay or options-page presentation and interaction
- `infrastructure` — installation, packaging, runtime, or developer tooling

Create labels on first use with `gh label create` if they do not already exist.

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.
