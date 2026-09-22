# fuzeinfra-keepalived — keepalived plus the tools the API active-active VIP
# DaemonSet needs at runtime (helm/fuzeinfra/templates/api-vip-keepalived.yaml).
#
# A stock keepalived image ships keepalived only. This DaemonSet's render.sh
# builds keepalived.conf with jq/bash, and notify.sh calls the Contabo API with
# curl/jq at failover — so the image MUST also carry bash + curl + jq (and
# iproute2 for VIP address management). This image is exactly that, nothing more.
FROM alpine:3.20

RUN apk add --no-cache \
      keepalived \
      bash \
      curl \
      jq \
      iproute2 \
      ca-certificates \
 && keepalived --version \
 && bash --version | head -1 \
 && curl --version | head -1 \
 && jq --version

# The DaemonSet sets its own command (exec /cfg/render.sh -> exec keepalived).
# This default is only for a bare `docker run` sanity check.
#
# Intentionally NO non-root USER: keepalived manages network interfaces (adds/
# removes the VIP, sends VRRP) via netlink and MUST run as root, and the DaemonSet
# runs privileged with NET_ADMIN. A non-root USER would break it — the root
# requirement is intrinsic here, not an oversight.
# nosemgrep: dockerfile.security.missing-user.missing-user
CMD ["keepalived", "--version"]
