---
id: debug-001
domain: application-observability-maintenance
topic: troubleshooting
difficulty: 2
points: 4
namespaces: ["{{ns}}"]
check_timeout: 120
vars:
  ns: [fix, broken, triage]
---

## Statement

In namespace `{{ns}}` the Deployment `api` (2 replicas) and its Service `api` are not working: the pods never become `Ready` and the Service has no endpoints.

Investigate (`kubectl get`, `describe`, `events`, `logs`) and fix **both** problems. Keep using an `nginx` image.

## Setup

```bash
kubectl create namespace {{ns}}
kubectl -n {{ns}} create deployment api --image=nginx:1.27-nonexistent --replicas=2
kubectl -n {{ns}} create service clusterip api --tcp=80:80
kubectl -n {{ns}} patch service api -p '{"spec":{"selector":{"app":"api-v2"}}}'
```

## Check

```
2|2 replicas of api are Ready|[ "$(kubectl -n {{ns}} get deploy api -o jsonpath='{.status.readyReplicas}')" = "2" ]
2|the Service api has 2 ready endpoints|[ "$(kubectl -n {{ns}} get endpointslices.discovery.k8s.io -l kubernetes.io/service-name=api -o jsonpath='{range .items[*].endpoints[*]}{.conditions.ready}{"\n"}{end}' | grep -c true)" = "2" ]
```

## Solution: set image and patch the selector

```bash
kubectl -n {{ns}} set image deployment/api nginx=nginx:1.27
kubectl -n {{ns}} patch service api -p '{"spec":{"selector":{"app":"api"}}}'
kubectl -n {{ns}} rollout status deployment/api --timeout=120s
```

## Solution: json patch and recreate the Service

```bash
kubectl -n {{ns}} patch deployment api --type=json -p '[{"op":"replace","path":"/spec/template/spec/containers/0/image","value":"nginx:1.27"}]'
kubectl -n {{ns}} delete service api
kubectl -n {{ns}} expose deployment api --port=80
kubectl -n {{ns}} rollout status deployment/api --timeout=120s
```
