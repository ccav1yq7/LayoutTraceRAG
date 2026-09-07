# CI installation

The release owner prepared and tested the code locally, but the current GitHub OAuth credential lacks the `workflow` scope. GitHub rejected the attempted workflow push.

`ci-workflow.yml` is an inactive template. To install it, use an account credential authorized to write GitHub Actions workflows and place it under `.github/workflows/checks.yml`. No GitHub CI success is claimed until an actual run completes.

CodeWeaver includes a real sandbox smoke check; this host cannot initialize bubblewrap network namespaces. Local rejection was fail-closed. A supported Linux runner must independently pass that smoke check.
