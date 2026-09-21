---
id: multicontainer-001
domain: application-design-build
topic: multi-container
difficulty: 2
points: 6
namespaces: ["{{ns}}"]
check_timeout: 60
vars:
  ns: [apps, pods, work]
  pod: [logger, shipper, agent]
---

## Statement

In namespace `{{ns}}`, create a Pod named `{{pod}}` that has:

- an **init container** `prep` (image `busybox:1.36`) that writes the word `ready` to the file `/work/status`;
- two regular containers, `app` (`busybox:1.36`, runs `sleep 3600`) and `reader` (`busybox:1.36`, prints `/work/status` and then runs `sleep 3600`);
- an `emptyDir` volume named `shared`, mounted at `/work` in all three containers.

The Pod must end up `Ready`.

## Setup

```bash
kubectl create namespace {{ns}}
```

## Check

```
1|Pod {{pod}} has the init container prep|[ "$(kubectl -n {{ns}} get pod {{pod}} -o jsonpath='{.spec.initContainers[*].name}')" = "prep" ]
1|Pod has the containers app and reader|[ "$(kubectl -n {{ns}} get pod {{pod}} -o jsonpath='{.spec.containers[*].name}')" = "app reader" ]
1|volume shared is an emptyDir|kubectl -n {{ns}} get pod {{pod}} -o jsonpath='{.spec.volumes[?(@.name=="shared")]}' | grep -q emptyDir
2|Pod is Ready (both containers running)|kubectl -n {{ns}} wait --for=condition=Ready pod/{{pod}} --timeout=60s
1|app sees the file written by the init container|[ "$(kubectl -n {{ns}} exec {{pod}} -c app -- cat /work/status)" = "ready" ]
```

## Solution: command form

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: {{pod}}
  namespace: {{ns}}
spec:
  volumes:
    - name: shared
      emptyDir: {}
  initContainers:
    - name: prep
      image: busybox:1.36
      command: ["sh", "-c", "echo ready > /work/status"]
      volumeMounts:
        - name: shared
          mountPath: /work
  containers:
    - name: app
      image: busybox:1.36
      command: ["sleep", "3600"]
      volumeMounts:
        - name: shared
          mountPath: /work
    - name: reader
      image: busybox:1.36
      command: ["sh", "-c", "cat /work/status; sleep 3600"]
      volumeMounts:
        - name: shared
          mountPath: /work
EOF
```

## Solution: args form

```bash
kubectl create -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: {{pod}}
  namespace: {{ns}}
spec:
  initContainers:
    - name: prep
      image: busybox:1.36
      command: ["sh"]
      args: ["-c", "echo ready > /work/status"]
      volumeMounts:
        - {name: shared, mountPath: /work}
  containers:
    - name: app
      image: busybox:1.36
      args: ["sleep", "3600"]
      volumeMounts:
        - {name: shared, mountPath: /work}
    - name: reader
      image: busybox:1.36
      command: ["sh"]
      args: ["-c", "cat /work/status; exec sleep 3600"]
      volumeMounts:
        - {name: shared, mountPath: /work}
  volumes:
    - name: shared
      emptyDir: {}
EOF
```
