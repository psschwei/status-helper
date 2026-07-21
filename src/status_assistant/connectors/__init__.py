"""GitHub connectors.

The package boundary that isolates the rest of the app from any specific GitHub client
library. Everything outside this package works with the domain models in
``status_assistant.models`` and the ``GitHubConnector`` protocol — never with a vendor type.
"""
