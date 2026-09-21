---
id: kustomize-001
domain: application-deployment
topic: kustomize
difficulty: 2
points: 5
namespaces: ["{{ns}}"]
check_timeout: 90
vars:
  ns: [kust, overlay, envs]
  prefix: [demo, acme]
  env: [staging, qa]
---

## Statement

Use Kustomize to deploy an application. Create a directory with a base manifest and a `kustomization.yaml`.

- The base manifest is a Deployment named `web` (image `nginx:1.27`, container port 80, label `app: web`) with **1** replica.
- The kustomization must: put everything in namespace `{{ns}}`, prefix all names with `{{prefix}}-`, add the label `env: {{env}}` to the resources, and set the replicas to **3**.

Apply it with `kubectl apply -k`.

## Setup

```bash
kubectl create namespace {{ns}}
```

## Check

```
1|Deployment {{prefix}}-web exists in {{ns}}|kubectl -n {{ns}} get deploy {{prefix}}-web
1|image is nginx:1.27|[ "$(kubectl -n {{ns}} get deploy {{prefix}}-web -o jsonpath='{.spec.template.spec.containers[0].image}')" = "nginx:1.27" ]
1|Deployment has the label env={{env}}|[ "$(kubectl -n {{ns}} get deploy {{prefix}}-web -o jsonpath='{.metadata.labels.env}')" = "{{env}}" ]
1|replicas are set to 3|[ "$(kubectl -n {{ns}} get deploy {{prefix}}-web -o jsonpath='{.spec.replicas}')" = "3" ]
1|3 replicas are Ready|kubectl -n {{ns}} rollout status deploy/{{prefix}}-web --timeout=90s >/dev/null && [ "$(kubectl -n {{ns}} get deploy {{prefix}}-web -o jsonpath='{.status.readyReplicas}')" = "3" ]
```

## Solution: labels and replicas transformers

```bash
dir=$(mktemp -d)
cat > "$dir/deploy.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: nginx
          image: nginx:1.27
          ports:
            - containerPort: 80
EOF
cat > "$dir/kustomization.yaml" <<EOF
namespace: {{ns}}
namePrefix: {{prefix}}-
resources:
  - deploy.yaml
labels:
  - pairs:
      env: {{env}}
    includeSelectors: true
replicas:
  - name: web
    count: 3
EOF
kubectl apply -k "$dir"
rm -rf "$dir"
```

## Solution: commonLabels and a patch

```bash
dir=$(mktemp -d)
cat > "$dir/deploy.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: nginx
          image: nginx:1.27
EOF
cat > "$dir/kustomization.yaml" <<EOF
namespace: {{ns}}
namePrefix: {{prefix}}-
commonLabels:
  env: {{env}}
resources:
  - deploy.yaml
patches:
  - target:
      kind: Deployment
      name: web
    patch: |-
      - op: replace
        path: /spec/replicas
        value: 3
EOF
kubectl apply -k "$dir"
rm -rf "$dir"
```
