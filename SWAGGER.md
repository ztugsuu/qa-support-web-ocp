# Swagger directory

Open `/swagger`. Select `test-ms` or `pre-new-ms`, search by service/route
name, and open a Unitel or Mogul link in a new browser tab.

The backend reads actual OpenShift route hosts and TLS settings. The default
Swagger path is `/{service}/swagger-ui/swagger-ui/index.html`, where `service`
is the route's `spec.to.name`. Links are generated, not health checked.
`spec.path` is returned for reference; it is not prepended because the Swagger
path template is the complete path from the host root.

## Cluster access

Provide a dedicated kubeconfig with contexts named `testocp` and `testmogul`.
Each context must use credentials for its own cluster and a trusted CA.
Mount that file read-only into the web container, for example at
`/etc/qa-swagger/kubeconfig`, and set:

```
QA_SWAGGER_KUBECONFIG=/etc/qa-swagger/kubeconfig
```

The app explicitly passes `--kubeconfig` and `--context` on each query; it never
switches the current context or logs in. Existing pod/QA features are unaffected.
Do not commit kubeconfig credentials. Store the file in an OpenShift Secret
and mount it only in this deployment. Configure the environment variable and
volume mount when the Secret is ready. `manifests/deploy.yaml` now includes
the environment variable and mount for Secret `qa-swagger-kubeconfig`.
Create this Secret in the hosting cluster before applying the Deployment.
Without the Secret the updated pod cannot start.

Copy `manifests/swagger.kubeconfig.example` to `manifests/swagger.kubeconfig`.
Fill the two cluster CA values and dedicated route-reader tokens locally.
The completed file is gitignored. The example is not deployed by Kustomize.
Verify both contexts (repeat with `pre-new-ms`):

```powershell
oc --kubeconfig=manifests/swagger.kubeconfig --context=testocp get routes -n test-ms
oc --kubeconfig=manifests/swagger.kubeconfig --context=testmogul get routes -n test-ms
```

On the **cluster hosting this web app**, using its explicit context name in
your normal administrative kubeconfig:

```powershell
# Replace HOSTING_CONTEXT with the actual context name from oc config get-contexts.
oc --context=HOSTING_CONTEXT -n pre-new-ms create secret generic qa-swagger-kubeconfig --from-file=kubeconfig=manifests/swagger.kubeconfig --dry-run=client -o yaml | oc --context=HOSTING_CONTEXT -n pre-new-ms apply -f -
oc --context=HOSTING_CONTEXT apply -k manifests
oc --context=HOSTING_CONTEXT -n pre-new-ms rollout status deployment/qa-support-web-pod
```

After replacing Secret contents later, restart the web deployment so `oc`
uses the refreshed credentials immediately. Do not paste the filled file or
tokens into chat or logs.

In **each cluster**, grant the chosen service account `get` and `list` access
to `routes` in API group `route.openshift.io` in **both** namespaces. Example
Role, applied separately in `test-ms` and `pre-new-ms`, with a RoleBinding to
the account used in that cluster's kubeconfig:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: qa-swagger-route-reader
rules:
  - apiGroups: [route.openshift.io]
    resources: [routes]
    verbs: [get, list]
```

The web pod needs network access to both cluster APIs. Developers' browsers
need DNS/network access to the route hosts. Remote account credentials need
an appropriate rotation process.

## Path override

Set `QA_SWAGGER_PATH_TEMPLATE` to override the complete path globally.
Supported placeholders are `{service}` and `{route}`. Example:
`/{service}/swagger-ui/swagger-ui/index.html`. Per-service exceptions are
not currently configured.
