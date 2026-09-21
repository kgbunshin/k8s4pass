# Student shell: runs INSIDE a container, with only kubectl and the kubeconfig
# of the session's own cluster. kubectl version = kind node version.
FROM ubuntu:24.04

ARG KUBECTL_VERSION=v1.35.0

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash-completion ca-certificates curl jq less procps tmux vim \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
 && echo "$(curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256")  /usr/local/bin/kubectl" | sha256sum -c - \
 && chmod +x /usr/local/bin/kubectl

USER ubuntu
WORKDIR /home/ubuntu
RUN mkdir -p /home/ubuntu/.kube
COPY --chown=ubuntu:ubuntu bashrc /home/ubuntu/.bashrc
COPY --chown=ubuntu:ubuntu vimrc /home/ubuntu/.vimrc
