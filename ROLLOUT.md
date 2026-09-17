# Rollout restart

Additional QA tools, deployment steps and audit/smoke configuration are documented
in [QA-FEATURES.md](QA-FEATURES.md).

The pod dashboard resolves Pod → ReplicaSet → Deployment using controller UIDs.
Every row belonging to the same deployment shares restart eligibility.

Restart is enabled only when the deployment controller has observed the current
generation, all desired replicas are updated, ready and available, and all owned
pods and containers are ready. Pending, terminating, missing, failed or crashing
pods block restart, as do paused deployments and zero replicas. Completed regular
init containers are accepted; native sidecars must be running and ready.

Containers with a nonzero restart count must have run continuously for 300 seconds
since their latest start. This prevents the button becoming enabled during brief
ready periods in a crash loop. Historical restarts do not block a recovered pod
forever. The threshold is `RESTART_STABILITY_SECONDS` in `rollout.py`.

The dashboard refreshes every 5 seconds while visible and disables restart if
refresh fails. The POST endpoint independently rechecks live state and returns
409 for an ineligible deployment. It updates the same pod-template annotation as
`oc rollout restart`, using a JSON Patch resourceVersion test so concurrent
requests cannot both modify the same deployment version. Cluster health can
change after any snapshot; checks do not guarantee future application health.

## Deployment

Build and publish a new image, then set its tag in `manifests/deploy.yaml` using
the existing release process. The Dockerfile includes the new `rollout.py` module.

The existing `qa-support-sa` service account needs these namespace-scoped RBAC
permissions in both `test-ms` and `pre-new-ms`:

| API group | Resource | Verbs |
| --- | --- | --- |
| core | pods | get, list |
| apps | replicasets | get, list |
| apps | deployments | get, list, patch |

If deployment or ReplicaSet reads fail, pods remain visible but restart stays
disabled. Existing logs/metrics permissions are still needed for their respective
buttons.

### test-ms restart permission

The dashboard runs as `system:serviceaccount:pre-new-ms:qa-support-sa`, including
when `test-ms` is selected in the UI. The existing `test-ms/view-0` binding provides
read access but does not grant deployment patch access. The supplemental manifest
grants only `patch` on `apps/deployments` in `test-ms` to that service account:

```sh
oc apply -f manifests/rbac/test-ms-rollout.yaml
oc auth can-i patch deployments.apps -n test-ms --as=system:serviceaccount:pre-new-ms:qa-support-sa
```

The verification command should print `yes`. Apply this manifest separately;
do not add it to `manifests/kustomization.yaml`, whose namespace transformer
targets `pre-new-ms`. No image rebuild or dashboard restart is required for this
RBAC change. Deployment health checks still apply.

## Checks

`python -m unittest discover -s tests -v`

`node --test tests/dashboard.test.cjs`

Tests mock cluster commands; they do not restart real workloads.

References: [Deployment completion](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/),
[Pod readiness and container states](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/).
