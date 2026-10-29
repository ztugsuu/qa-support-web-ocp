# Swagger-only web

`/` and `/swagger` show only the Swagger directory. Previous pod polling,
logs, restart, QA tools and database endpoints are no longer registered.
The previous source files remain in the repository for reference.

The server returns an in-memory snapshot immediately and refreshes routes in
the background. Each cluster/namespace pair is queried at most once per 60
seconds during successful operation, regardless of visitor count. Refresh
failures retain the last successful data and back off for 15 seconds. A cold
cache displays a loading state. Browser retries stop after about 16 seconds;
the refresh button reads the shared cache rather than forcing a cluster call.
There is no continuous browser polling. Cache is lost on pod restart.

Gunicorn uses one process with 16 request threads to share the cache, plus at
most four background cluster queries. Each query has a 12 second subprocess
timeout. `/healthz` probes do not query clusters. Swagger links open directly
in developers' browsers; their API traffic does not pass through this web.

## Deploy

Build and push a new image from this Dockerfile and set its new immutable tag
in `manifests/deploy.yaml`. Applying manifests with an old image will not
activate this code. No database Secret is needed now.

The dedicated kubeconfig must contain contexts `testocp` and `testmogul`, with
route get/list credentials for `test-ms` and `pre-new-ms` on each cluster.
On the cluster hosting this web, create/update the Secret before deploying:

```powershell
oc -n pre-new-ms create secret generic qa-swagger-kubeconfig --from-file=kubeconfig=manifests/swagger.kubeconfig --dry-run=client -o yaml | oc -n pre-new-ms apply -f -
oc apply -k manifests
oc -n pre-new-ms rollout status deployment/qa-support-web-pod
```

The file is mounted read-only at `/etc/qa-swagger/kubeconfig` and referenced by
`QA_SWAGGER_KUBECONFIG`. Never commit the filled kubeconfig.

The default path is `/{service}/swagger-ui/swagger-ui/index.html` with the
actual route service name, host and TLS. `QA_SWAGGER_PATH_TEMPLATE` overrides
the complete path globally; `{service}` and `{route}` are supported.

The concurrency test simulates 30 simultaneous cache clients with a delayed
cluster query. It does not establish real OpenShift HTTP capacity; validate
30 concurrent browsers against the deployed image before treating that as a
production capacity guarantee. Investigate router/pod logs if 504s persist.
