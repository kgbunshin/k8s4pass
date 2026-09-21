---
id: securitycontext-001
domain: application-environment-config-security
topic: security-context
difficulty: 2
points: 7
namespaces: ["{{ns}}"]
check_timeout: 60
vars:
  ns: [secure, locked, hardened]
  pod: [safe-app, sealed]
  uid: [1001, 2000, 3000]
---

## Statement

In namespace `{{ns}}`, create a hardened Pod named `{{pod}}` (image `busybox:1.36`, command `sleep 3600`) that:

- runs as user ID `{{uid}}` and must not be allowed to run as root (`runAsNonRoot`);
- has a read-only root filesystem;
- does not allow privilege escalation;
- drops **all** Linux capabilities.

## Setup

```bash
kubectl create namespace {{ns}}
```

## Check

```
1|Pod {{pod}} is Ready|kubectl -n {{ns}} wait --for=condition=Ready pod/{{pod}} --timeout=60s
2|the container runs as uid {{uid}}|[ "$(kubectl -n {{ns}} exec {{pod}} -- id -u)" = "{{uid}}" ]
1|runAsNonRoot is true|kubectl -n {{ns}} get pod {{pod}} -o yaml | grep -q 'runAsNonRoot: true'
1|readOnlyRootFilesystem is true|[ "$(kubectl -n {{ns}} get pod {{pod}} -o jsonpath='{.spec.containers[0].securityContext.readOnlyRootFilesystem}')" = "true" ]
1|allowPrivilegeEscalation is false|[ "$(kubectl -n {{ns}} get pod {{pod}} -o jsonpath='{.spec.containers[0].securityContext.allowPrivilegeEscalation}')" = "false" ]
1|all capabilities are dropped|[ "$(kubectl -n {{ns}} get pod {{pod}} -o jsonpath='{.spec.containers[0].securityContext.capabilities.drop[*]}')" = "ALL" ]
```

## Solution: container-level context

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: {{pod}}
  namespace: {{ns}}
spec:
  containers:
    - name: app
      image: busybox:1.36
      command: ["sleep", "3600"]
      securityContext:
        runAsUser: {{uid}}
        runAsNonRoot: true
        readOnlyRootFilesystem: true
        allowPrivilegeEscalation: false
        capabilities:
          drop: ["ALL"]
EOF
```

## Solution: pod-level user and container-level hardening

```bash
kubectl create -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: {{pod}}
  namespace: {{ns}}
spec:
  securityContext:
    runAsUser: {{uid}}
    runAsNonRoot: true
  containers:
    - name: main
      image: busybox:1.36
      args: ["sleep", "3600"]
      securityContext:
        readOnlyRootFilesystem: true
        allowPrivilegeEscalation: false
        capabilities:
          drop:
            - ALL
EOF
```
