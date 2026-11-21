# Blockhost on k3s / Kubernetes

Manifests are in [`deploy/kubernetes/`](../deploy/kubernetes): a namespace, a 20 Gi PVC, a single-replica Deployment, and two Services (web on TCP 8080, game on UDP 19132).

## Install

```bash
kubectl apply -k "https://github.com/buschmjm/blockhost//deploy/kubernetes?ref=main"
# or from a clone: kubectl apply -k deploy/kubernetes
kubectl -n blockhost rollout status deploy/blockhost
kubectl -n blockhost logs deploy/blockhost | grep "Setup code"
kubectl -n blockhost port-forward svc/blockhost-web 8080:8080
```

Open http://localhost:8080, enter the setup code, set a password, accept the EULA.

To skip the setup code, create the password secret before applying:

```bash
kubectl create namespace blockhost
kubectl -n blockhost create secret generic blockhost-admin --from-literal=password='a-long-password'
```

## Players

`blockhost-game` is a `LoadBalancer` Service on UDP 19132:

- **k3s:** ServiceLB gives it the node's IP. Players connect to that IP on 19132.
- **MetalLB / cloud:** it gets an external IP: `kubectl -n blockhost get svc blockhost-game`.
- **Neither:** change `type: LoadBalancer` to `NodePort` and use `<node-ip>:<nodePort>`.

## Web page over HTTPS

Copy [`ingress.example.yaml`](../deploy/kubernetes/ingress.example.yaml), set your host name and TLS issuer, and apply it. Blockhost marks its session cookie `Secure` when the proxy sends `X-Forwarded-Proto: https` (Traefik and ingress-nginx do).

If you already run an SSO proxy (oauth2-proxy, Authelia, Authentik) in front of it, you can set `BLOCKHOST_AUTH=none` on the Deployment. Then make sure the web Service is reachable **only** through that proxy.

## Things to know

- Keep `replicas: 1` and `strategy: Recreate`. Two pods on one world will corrupt it.
- The pod is pinned to `kubernetes.io/arch: amd64`.
- `terminationGracePeriodSeconds: 90` gives the server time to save on shutdown.
- Back up the PVC (Velero, Longhorn snapshots, or the **Download** button per world).

## Join addresses

Set `BLOCKHOST_LAN_ADDRESS` in the Deployment to the `blockhost-game` Service's address so the dashboard shows it. Router UPnP doesn't work from pod networking; use the playit.gg tunnel or enter the address yourself on the Network tab. See [networking.md](networking.md).
