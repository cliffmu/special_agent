# Special Agent

This is an early scaffold of the Special Agent Home Assistant integration.

The project is gradually migrating to an agent-based architecture. See
[`migration_to_agent_plan.md`](migration_to_agent_plan.md) for the complete
roadmap.

Install by copying this repository into your `custom_components/special_agent`
folder and restart Home Assistant. Use the `special_agent.reload` service to
reload the integration after making changes.

## Debug logging
Go to Settings \u25b8 Devices & Services \u25b8 Special\u00a0Agent \u25b8 3-dot menu \u25b8 Enable debug logging.
Switch off to return to normal (INFO) logging.

## Vector index utilities
`utils/vector_index.py` includes helpers to build and query a simple NumPy-based
index of Home Assistant entities. `async_load_vector_index` asynchronously loads
the saved index using a background thread (or Home Assistant's executor when
available).

## Privacy and safe contributions

Keep real Home Assistant configuration, conversation/session exports, device
inventories, logs, recordings, and credentials outside this repository. Use
synthetic data in examples and documentation. Ignore rules also protect common
private exports, but they do not remove data that has already been committed.

Before committing from a new clone, install [Gitleaks](https://github.com/gitleaks/gitleaks#installing)
and enable the repository hook:

```sh
git config core.hooksPath .githooks
```

The hook checks staged files for ignored/private exports, credentials, personal
email addresses, and local user paths. GitHub Actions repeats these checks on
pushes and pull requests, including fetched history. Use your GitHub noreply email
for commits; configure it using [GitHub's instructions](https://docs.github.com/en/account-and-profile/how-tos/email-preferences/setting-your-commit-email-address).

After a privacy-related history rewrite, use a fresh clone or carefully reset
local branches to the rewritten remote branches. Do not merge or push old history
back into this repository. Keep any recovery copy private and outside Git remotes.
