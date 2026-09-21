---
id: probes-001
domain: application-observability-maintenance
topic: probes-resources
difficulty: 2
points: 8
namespaces: ["{{ns}}"]
vars:
  ns: [api, backend, svc]
  replicas: [2, 3]
---

## Statement

In namespace `{{ns}}` the deployment `api` exists (image `nginx:1.27`, {{replicas}} replicas).
Change it so that:

- it has an HTTP GET `readinessProbe` on `/` at port `80`, with `initialDelaySeconds: 5` and `periodSeconds: 10`;
- each container requests `cpu: 50m` and `memory: 64Mi`, with limits of `cpu: 100m` and `memory: 128Mi`;
- all {{replicas}} replicas end up `Ready`.

## Setup

```bash
kubectl create namespace {{ns}}
kubectl -n {{ns}} create deployment api --image=nginx:1.27 --replicas={{replicas}}
```

## Check

```
1|readinessProbe httpGet path /|[ "$(kubectl -n {{ns}} get deploy api -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.httpGet.path}')" = "/" ]
1|readinessProbe on port 80|[ "$(kubectl -n {{ns}} get deploy api -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.httpGet.port}')" = "80" ]
1|initialDelaySeconds 5 and periodSeconds 10|[ "$(kubectl -n {{ns}} get deploy api -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.initialDelaySeconds} {.spec.template.spec.containers[0].readinessProbe.periodSeconds}')" = "5 10" ]
2|requests cpu=50m memory=64Mi|[ "$(kubectl -n {{ns}} get deploy api -o jsonpath='{.spec.template.spec.containers[0].resources.requests.cpu} {.spec.template.spec.containers[0].resources.requests.memory}')" = "50m 64Mi" ]
2|limits cpu=100m memory=128Mi|[ "$(kubectl -n {{ns}} get deploy api -o jsonpath='{.spec.template.spec.containers[0].resources.limits.cpu} {.spec.template.spec.containers[0].resources.limits.memory}')" = "100m 128Mi" ]
1|{{replicas}} replicas Ready|kubectl -n {{ns}} rollout status deploy/api --timeout=90s >/dev/null && [ "$(kubectl -n {{ns}} get deploy api -o jsonpath='{.status.readyReplicas}')" = "{{replicas}}" ]
```

## Solution: kubectl set and patch

```bash
kubectl -n {{ns}} set resources deployment/api --requests=cpu=50m,memory=64Mi --limits=cpu=100m,memory=128Mi
kubectl -n {{ns}} patch deployment api -p '{"spec":{"template":{"spec":{"containers":[{"name":"nginx","readinessProbe":{"httpGet":{"path":"/","port":80},"initialDelaySeconds":5,"periodSeconds":10}}]}}}}'
```

## Solution: manifest

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata: { name: api, namespace: {{ns}} }
spec:
  replicas: {{replicas}}
  selector: { matchLabels: { app: api } }
  template:
    metadata: { labels: { app: api } }
    spec:
      containers:
        - name: nginx
          image: nginx:1.27
          readinessProbe:
            httpGet: { path: /, port: 80 }
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests: { cpu: 50m, memory: 64Mi }
            limits: { cpu: 100m, memory: 128Mi }
EOF
```
