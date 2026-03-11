# JupyterHub on OpenStack: Complete Build Guide

All lessons from two build cycles incorporated. Rebuild time: ~30 minutes.

---

## Step 0: Admin VM

From openstack dashboard:

1. Create a keypair (Compute → Key Pairs → Create) — download the `.pem` immediately
2. Launch a small instance (2GB RAM is plenty), Ubuntu 24.04, attach the keypair
3. Note the assigned IP

From your local machine:

```bash
chmod 600 ~/.ssh/<your-key>.pem

ssh -i ~/.ssh/<your-key>.pem ubuntu@<admin-ip>

# First thing: set a password so you can use the openstack vm console if locked out
sudo passwd ubuntu

sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-openstackclient
```

Download your OpenStack RC file from openstack (Project → API Access → Download
OpenStack RC File v3). Copy it to the admin VM and source it:

```bash
source ~/openrc.sh
# Enter your openstack password when prompted (available from Settings->User Settings can forever be renewed/forgotten til next time)
```

### Checkpoint 0

```bash
openstack server list
openstack flavor list -c Name -c RAM -c VCPUs -c Disk -f table | tee ~/flavor-notes.txt
openstack image list
openstack network list
openstack availability zone list
openstack volume type list
```

Write down your choices:
- [ ] Flavor: ____________
- [ ] Image: ____________
- [ ] Network: ____________ (check existing instances for the correct name)
- [ ] Keypair: ____________
- [ ] Availability zone: ____________
- [ ] Volume type: ____________

---

## Step 1: Security Group

```bash
openstack security group create jhub-sg \
  --description "JupyterHub server: SSH only"

# SSH only — HTTP is accessed via SSH tunnel
openstack security group rule create jhub-sg \
  --protocol tcp --dst-port 22 --remote-ip 0.0.0.0/0

# ICMP (ping) for debugging
openstack security group rule create jhub-sg \
  --protocol icmp --remote-ip 0.0.0.0/0
```

### Checkpoint 1

```bash
openstack security group show jhub-sg -f yaml | grep -i ingress
# 2 ingress rules: TCP 22, ICMP
# No HTTP/HTTPS exposed — all web access via SSH tunnel
```

---

## Step 2: Compute Instance

```bash
FLAVOR="<your-flavor>"
IMAGE="<your-ubuntu-image>"
NETWORK="<your-network>"
KEYPAIR="<your-keypair>"

openstack server create \
  --flavor "$FLAVOR" \
  --image "$IMAGE" \
  --network "$NETWORK" \
  --security-group jhub-sg \
  --key-name "$KEYPAIR" \
  --wait \
  jhub-server

# Get the assigned IP
openstack server show jhub-server -c addresses -f value
```

Set up SSH config on the admin VM (`~/.ssh/config`):

```
Host jhub-server
    HostName <jhub-server-ip>
    User ubuntu
    IdentityFile ~/.ssh/<your-key>.pem
```

### Checkpoint 2

```bash
ssh jhub-server 'hostname && free -h && nproc'
```

**Immediately set a password** on the compute instance:

```bash
ssh jhub-server
sudo passwd ubuntu
```

---

## Step 3: Storage

From the admin VM:

```bash
openstack volume create --size 500 \
  --availability-zone <your-az> \
  --type <your-volume-type> \
  jhub-user-data

openstack server add volume jhub-server jhub-user-data
```

SSH to jhub-server:

```bash
ssh jhub-server

lsblk
sudo mkfs.ext4 /dev/vdb
sudo mkdir -p /data/users
sudo mount /dev/vdb /data/users
echo '/dev/vdb /data/users ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
```

### Checkpoint 3

```bash
df -h /data/users
# ~500G mounted
```

---

## Step 4: Docker, k3s, Helm

All on jhub-server. **Important**: Move Docker and k3s storage to the Cinder
volume BEFORE building images (30GB root disk is too small).

```bash
# Install Docker
sudo apt install -y docker.io docker-buildx

# Move Docker storage to Cinder volume
sudo systemctl stop docker
sudo mv /var/lib/docker /data/users/docker
sudo ln -s /data/users/docker /var/lib/docker
sudo systemctl start docker

# Install k3s — disable Traefik (not needed, blocks port 80)
curl -sfL https://get.k3s.io | sh -s - \
  --write-kubeconfig-mode=644 \
  --disable traefik

# Move k3s storage to Cinder volume
sudo systemctl stop k3s
sudo mv /var/lib/rancher /data/users/rancher
sudo ln -s /data/users/rancher /var/lib/rancher
sudo systemctl start k3s

# Set up kubectl
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
export KUBECONFIG=~/.kube/config
echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc

# Install Helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

### Checkpoint 4

```bash
kubectl get nodes
# jhub-server   Ready    control-plane

kubectl get pods -A
# System pods Running (no Traefik)

helm version
```

---

## Step 5: Build Geospatial Image

MAKEFLAGS for parallel compilation. Posit Package Manager for binary R
packages (arrow, duckdb install in seconds not hours).

```bash
mkdir -p ~/images/geospatial
cat > ~/images/geospatial/Dockerfile << 'GEODOCK'
FROM rocker/geospatial:4.4

RUN apt-get update && apt-get install -y \
    curl git python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

ENV MAKEFLAGS="-j32"

RUN curl -fsSL https://code-server.dev/install.sh | sh

RUN pip3 install --break-system-packages \
    jupyterhub jupyterlab \
    jupyter-server-proxy \
    jupyter-rsession-proxy \
    jupyter-codeserver-proxy

RUN R -e ' \
  options(repos = c( \
    RSPM = "https://packagemanager.posit.co/cran/__linux__/jammy/latest", \
    CRAN = "https://cloud.r-project.org" \
  )); \
  install.packages(c("arrow", "duckdb")) \
'

RUN R -e '<other package installs here>'

ENV GDAL_HTTP_UNSAFESSL=YES
ENV GDAL_HTTP_CONNECTTIMEOUT=60
ENV GDAL_HTTP_TIMEOUT=120
ENV GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR
ENV GDAL_HTTP_MAX_RETRY=5
ENV GDAL_HTTP_RETRY_DELAY=1

CMD ["jupyterhub-singleuser"]
GEODOCK

cd ~/images/geospatial
sudo docker build -t inst/geospatial:latest . 2>&1 | tee build.log
```

Import into k3s:

```bash
sudo docker save inst/geospatial:latest -o /data/users/geospatial.tar
sudo k3s ctr images import /data/users/geospatial.tar
sudo rm /data/users/geospatial.tar
```

**Note**: If the build shows `<none>` tag in `sudo docker image ls`, tag
it manually: `sudo docker tag <image-id> inst/geospatial:latest`

### Checkpoint 5

```bash
sudo k3s ctr images list | grep inst/geospatial
```

---

## Step 6: Deploy JupyterHub

```bash
helm repo add jupyterhub https://hub.jupyter.org/helm-chart/
helm repo update
```

```bash
cat > ~/jhub-values.yaml << 'EOF'
proxy:
  service:
    type: LoadBalancer

hub:
  extraConfig:
    spawner: |
      c.KubeSpawner.notebook_dir = '/home/rstudio'
  config:
    Authenticator:
      admin_users:
        - <your-admin-username>
      allow_all: true
    NativeAuthenticator:
      open_signup: true
      minimum_password_length: 8
      ask_email_on_signup: true
    JupyterHub:
      authenticator_class: nativeauthenticator.NativeAuthenticator

singleuser:
  uid: 1000
  fsGid: 1000
  extraEnv:
    NB_USER: rstudio
  image:
    name: inst/geospatial
    tag: latest
    pullPolicy: IfNotPresent
  cpu:
    limit: 8
    guarantee: 2
  memory:
    limit: 24G
    guarantee: 4G
  storage:
    type: dynamic
    capacity: 10Gi
    homeMountPath: /home/rstudio
    dynamic:
      storageClass: local-path

prePuller:
  hook:
    enabled: false
  continuous:
    enabled: false

cull:
  enabled: true
  timeout: 3600
  every: 300

scheduling:
  userScheduler:
    enabled: false
EOF
```

Deploy:

```bash
helm upgrade --install jhub jupyterhub/jupyterhub \
  --namespace jhub \
  --create-namespace \
  --values ~/jhub-values.yaml \
  --timeout 10m
```

### Checkpoint 6

```bash
kubectl get pods -n jhub
# hub and proxy both Running
```

Access via SSH tunnel (see Access section below), log in, open a terminal:

```bash
R -e 'library(<some r package you care about>)'
```

Verify all three IDEs work:
- JupyterLab: `http://localhost:8080/user/<username>/lab`
- RStudio: `http://localhost:8080/user/<username>/rstudio/`
- code-server: `http://localhost:8080/user/<username>/codeserver/`

---

## Access: SSH Tunnel

No HTTP/HTTPS ports are exposed to the internet. All web access goes through
an SSH tunnel, which is encrypted, invisible to bots, and uses access already
permitted by institutional firewalls.

### Admin access

From any machine that can SSH to an openstack instance:

```bash
ssh -L 8080:localhost:80 jhub-server
```

Then browse to `http://localhost:8080`.

### User access

Each user sets up their `~/.ssh/config`:

```
Host jhub
    HostName <jhub-server-ip>
    User ubuntu
    IdentityFile ~/.ssh/<their-key-filename>
    LocalForward 8080 localhost:80
```

Then:

```bash
ssh jhub
```

Browse to `http://localhost:8080`. Sign up on first visit, log in after that.

---

## Onboarding a New User

### 1. User generates their SSH keypair

They run this on their own machine:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/jhub-key -N ""
```

This creates two files:
- `~/.ssh/jhub-key` — their private key (stays on their machine, never shared)
- `~/.ssh/jhub-key.pub` — their public key (they send this to you)

### 2. User sends you their public key

They email or message you the contents of `~/.ssh/jhub-key.pub`. It looks
like a single line:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@their-machine
```

This is safe to share — it's the public half, useless without the private key.

### 3. Admin adds the key to jhub-server

SSH to jhub-server and append their public key to authorized_keys:

```bash
ssh jhub-server

# Paste their public key into authorized_keys
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@their-machine' >> ~/.ssh/authorized_keys
```

Or if they sent you the `.pub` file:

```bash
# Copy it to jhub-server first, then:
cat /tmp/their-key.pub >> ~/.ssh/authorized_keys
```

Verify it's there:

```bash
cat ~/.ssh/authorized_keys
# Should show your key plus the new user's key
```

### 4. User configures their SSH

They add to their `~/.ssh/config`:

```
Host jhub
    HostName <jhub-server-ip>
    User ubuntu
    IdentityFile ~/.ssh/jhub-key
    LocalForward 8080 localhost:80
```

And fix permissions:

```bash
chmod 700 ~/.ssh/
chmod 600 ~/.ssh/jhub-key
```

### 5. User connects and signs up

```bash
ssh jhub
# Keep this terminal open

# In browser:
# http://localhost:8080
# Click "Sign up", choose username and password
# Log in
```

### Removing a user

Remove their public key from `~/.ssh/authorized_keys` on jhub-server
(delete the line), and unauthorize them at `http://localhost:8080/hub/authorize`.

---

## Admin Operations

### User management

```bash
# Admin panel (via tunnel)
http://localhost:8080/hub/admin

# Authorize/unauthorize users
http://localhost:8080/hub/authorize
```

### Shelve/unshelve (save resources when not in use)

OpenStack VMs reserve fixed compute resources whether idle or not. Shelving
frees compute and RAM while preserving disk state.

From admin VM:

```bash
# Shelve (like hibernate — frees resources, ~2 min to resume)
openstack server shelve jhub-server

# Unshelve (boots back, same IP, same state)
openstack server unshelve jhub-server
```

Optional cron on admin VM:

```bash
# Shelve at 7pm weekdays
0 19 * * 1-5 openstack server shelve jhub-server

# Unshelve at 7am weekdays
0 7 * * 1-5 openstack server unshelve jhub-server
```

### Backups

User PVCs are stored in `/data/users/rancher/storage/` on the host. Add
a nightly rsync to crontab on jhub-server:

```bash
sudo crontab -e

# Nightly backup
0 2 * * * rsync -a /data/users/rancher/storage/ /data/users/backups/daily/

# Weekly snapshot
0 3 * * 0 rsync -a /data/users/rancher/storage/ /data/users/backups/weekly-$(date +\%U)/
```

**Tell users**: this is a compute environment, not long-term storage.
Important work should be pushed to git or copied to institutional storage.

### Monitoring

```bash
# Check pod status
kubectl get pods -n jhub

# Hub logs (auth issues, spawner errors)
kubectl logs -n jhub -l component=hub --tail=50

# User pod logs
kubectl logs -n jhub jupyter-<username> --tail=50

# Resource usage
kubectl top pods -n jhub
```

### Rebuilding the Docker image

Edit `~/images/geospatial/Dockerfile`, then:

```bash
cd ~/images/geospatial
sudo docker build -t inst/geospatial:latest . 2>&1 | tee build.log
sudo docker save inst/geospatial:latest -o /data/users/geospatial.tar
sudo k3s ctr images import /data/users/geospatial.tar
sudo rm /data/users/geospatial.tar
```

Users stop/start their server to pick up the new image.

### Updating JupyterHub config

Edit `~/jhub-values.yaml`, then:

```bash
helm upgrade jhub jupyterhub/jupyterhub \
  --namespace jhub \
  --values ~/jhub-values.yaml
```

---

## Teardown

### JupyterHub only

```bash
helm uninstall jhub -n jhub
kubectl delete namespace jhub
```

### k3s only

```bash
/usr/local/bin/k3s-uninstall.sh
```

### Everything

From admin VM:

```bash
openstack server delete jhub-server
openstack volume delete jhub-user-data      # WARNING: destroys user data
openstack security group delete jhub-sg
```

### Full rebuild time: ~30 minutes

---

## Gotchas Reference

| Problem | Fix |
|---|---|
| SSH refuses key | `chmod 600 key.pem`, `chmod 700 ~/.ssh/` |
| Locked out of VM | Always `sudo passwd ubuntu` after first login |
| `openstack server create` fails with network error | Check network name matches existing instances (`openstack server list`) |
| Volume create fails | Need `--availability-zone` and `--type` |
| Root disk full (30GB) | Move Docker + k3s to Cinder BEFORE building images |
| `sudo <()` doesn't work | Use temp file: `docker save -o file.tar` then `k3s ctr images import file.tar` |
| Docker tag missing after build | Tag manually: `sudo docker tag <id> name:tag` |
| LoadBalancer pending | Port 80 conflict with Traefik — disable at k3s install |
| Image puller errors | Disable pre-puller for local-only images |
| Helm upgrade fails | Delete stuck pods first, retry |
| MAKEFLAGS | Set `ENV MAKEFLAGS="-j32"` in Dockerfile — saves hours on arrow/duckdb |
| RStudio 500 error | Check rserver.conf for invalid options; check user/permissions |
| JupyterLab permission denied | Set `notebook_dir` to `/home/rstudio` and `homeMountPath` to match |
| Files don't survive stop/start | Set `storage.homeMountPath: /home/rstudio` in values |
| Signed-up user can't log in | Add `allow_all: true` under Authenticator config |
| HTTP blocked by institutional firewall | Use SSH tunnel: `ssh -L 8080:localhost:80 jhub-server` |
| `pip install` packaging conflict in rocker | Use `--ignore-installed` or separate Python from R image |
| `sed` deleting too much | Write the full Dockerfile from scratch instead of sed on pip lines |

---

## State Tracker

```
[x] Step 0: Admin VM ready, OpenStack CLI working
[x] Step 1: Security group created (SSH + ICMP only)
[x] Step 2: Compute instance running, SSH works
[x] Step 3: Cinder volume mounted at /data/users
[x] Step 4: Docker + k3s + Helm on Cinder volume
[x] Step 5: Geospatial image built and imported
[x] Step 6: JupyterHub running, R/GDAL/hypertidy verified
[x] Step 7b: code-server + RStudio + JupyterLab all working
[x] Step 7c: NativeAuthenticator with signup/approve
[x] Step 7e: Persistent user storage on Cinder volume
[ ] Step 7d: Shared data mounted in user pods
```

---

## Future Steps

- **7d: Shared data mount** — mount existing research data read-only into all pods
- **DNS + TLS** — request a DNS name from ICT, add cert-manager for HTTPS
- **Docker image variants** — GDAL master from gdal-r-ci, Python-focused image
- **OAuth** — swap NativeAuthenticator for institutional identity provider
- **Phase 2: Multi-node k8s** — autoscaling, HPC bridge, EC2 expansion
