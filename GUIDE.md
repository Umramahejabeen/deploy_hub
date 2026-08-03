# DeployHub — Full Setup Guide

A self-service mini deployment portal: users submit a GitHub repo URL, the
app clones it, builds a Docker image, and runs it on your EC2 instance —
a tiny "Heroku clone." The portal itself is deployed via a Jenkins CI/CD
pipeline triggered by a GitHub webhook.

---

## 0. Tech stack

| Layer | Tool |
|---|---|
| Backend | Python, Flask, Flask-SQLAlchemy, docker-py SDK |
| Frontend | Jinja2 templates + Bootstrap 5 (server-rendered, no separate build step) |
| Database | SQLite (file-based, simple for a student project) |
| Containerization | Docker (portal + every deployed app) |
| Compute | AWS EC2 (Ubuntu 22.04) |
| Storage | AWS S3 (deployment logs archive) |
| IAM | Instance role for least-privilege S3 access, separate CI/CD user |
| CI/CD | Jenkins (on your Windows machine) — no Docker needed on this machine |
| Trigger | GitHub Webhook + ngrok (to expose local Jenkins to GitHub) |

> **Design choice:** Docker only ever runs on EC2 — never on your Windows
> machine. Jenkins builds the image *remotely* over SSH, using EC2's own
> Docker daemon (`docker compose up --build`). This means no Docker Hub
> account, no image push/pull step, and no Docker Desktop install on Windows
> at all. Jenkins on Windows only needs Git, Python, and an SSH client
> (Windows 10/11 ships with OpenSSH client built in).

---

## PHASE 1 — AWS Setup

### 1.1 Create an IAM user for yourself (don't use root)
1. AWS Console → IAM → Users → **Add user**
2. Name: `devops-student`
3. Attach policy: `AdministratorAccess` (fine for a learning project; in production you'd scope this down)
4. Save the Access Key + Secret Key somewhere safe (you'll need it for AWS CLI, not for the app itself)

### 1.2 Create the S3 bucket (for deployment logs)
1. S3 → **Create bucket**
2. Name: `deployhub-logs-bucket-<yourname>` (must be globally unique — update this name everywhere in the code/policy)
3. Region: pick one close to you, e.g. `ap-south-1` (Mumbai)
4. Block all public access: **Keep enabled** (logs are private)
5. Enable **Bucket Versioning** (optional but good practice to show off)
6. Create

### 1.3 Create an IAM Role for the EC2 instance
This is the key IAM concept to demonstrate: **the EC2 instance itself gets
permission to write to S3 — no access keys stored in code.**

1. IAM → Roles → **Create role**
2. Trusted entity: **AWS service** → **EC2**
3. Skip attaching AWS managed policies for now → Create role, name it `DeployHub-EC2-Role`
4. Open the role → **Add permissions → Create inline policy** → JSON tab
5. Paste the contents of `iam-policy-ec2-s3.json` (update the bucket name to match yours)
6. Save as `DeployHub-S3-Access`

### 1.4 Launch the EC2 instance
1. EC2 → **Launch instance**
2. Name: `deployhub-server`
3. AMI: **Ubuntu Server 22.04 LTS** (free tier eligible)
4. Instance type: `t2.micro` (free tier) — if builds feel slow, `t3.small` is cheap and much faster
5. Key pair: create new, name it `deployhub-key`, download the `.pem` file — **keep this safe, you need it for SSH and Jenkins**
6. Network settings → Edit security group, add inbound rules:
   | Type | Port | Source |
   |---|---|---|
   | SSH | 22 | anywhere |
   | Custom TCP | 5000 | Anywhere (0.0.0.0/0) — portal UI |
   | Custom TCP | 9000-9100 | Anywhere (0.0.0.0/0) — deployed apps |
7. Storage: 20 GB (default 8GB is tight once you're building multiple Docker images)
8. Advanced → **IAM instance profile** → select `DeployHub-EC2-Role`
9. Launch instance

### 1.5 Connect and install Docker on EC2
SSH in (from Windows, use PowerShell or PuTTY):
```bash
ssh -i deployhub-key.pem ubuntu@<EC2_PUBLIC_IP>
```

Install Docker:
```bash
sudo apt update && sudo apt upgrade -y
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker ubuntu
newgrp docker
docker --version
```

Install Docker Compose plugin:
```bash
sudo apt install -y docker-compose-plugin
docker compose version
```

Verify the IAM role is attached correctly (no keys needed!):
```bash
curl http://169.254.169.254/latest/meta-data/iam/security-credentials/
# should print: DeployHub-EC2-Role
```

Create the deploy folder:
```bash
mkdir ~/deployhub && cd ~/deployhub
```
You'll place `docker-compose.yml` here later (Jenkins will manage this remotely, but do one manual test first — see Phase 4).

---

## PHASE 2 — Backend & Frontend (already built for you)

Your project folder structure:
```
deployhub/
├── app.py                  # Flask routes
├── deploy_manager.py       # clone/build/run/S3-upload logic
├── models.py                # SQLAlchemy Deployment model
├── requirements.txt
├── Dockerfile                # builds the PORTAL itself
├── docker-compose.yml       # runs the portal on EC2
├── Jenkinsfile               # CI/CD pipeline definition
├── iam-policy-ec2-s3.json
├── templates/
│   ├── base.html
│   ├── index.html
│   └── dashboard.html
└── tests/
    └── test_app.py
```

### 2.1 Test on EC2 (not locally — Docker lives on EC2, not your Windows machine)

`deploy_manager.py` needs a live Docker daemon (it calls `docker.from_env()`),
so testing happens by SSHing into EC2 and running the app there, using
Docker that's already installed on the instance.

```bash
# On EC2:
cd ~/deployhub
git clone https://github.com/<your-username>/deployhub.git .
docker compose up -d --build
```

This builds the portal image using EC2's Docker and starts it, with the
Docker socket mounted so the portal can launch containers for whatever
repos users submit. Check it came up:

```bash
docker compose ps
docker compose logs -f deployhub    # watch it start; Ctrl+C to stop tailing
```

Open `http://<EC2_PUBLIC_IP>:5000` in your browser (from your Windows machine
— no SSH needed for this part, since port 5000 is open in the security
group). Submit a small public repo (e.g. a simple Flask "hello world" repo)
and watch the dashboard update through QUEUED → CLONING → BUILDING → RUNNING.

To make a code change and re-test:
```bash
# On EC2, after editing files or after a git pull:
docker compose up -d --build
```

> Since the EC2 instance has the `DeployHub-EC2-Role` IAM role attached,
> S3 log uploads work automatically here — no AWS keys to configure, unlike
> if you were testing on a machine without that role.

---

## PHASE 3 — Push to GitHub

```bash
cd deployhub
git add .
git commit -m "Initial commit: DeployHub portal"
git branch -M main
git remote add origin https://github.com/<your-username>/deployhub.git
git push -u origin main
```

---

## PHASE 4 — Manual first deploy to EC2 (sanity check before automating)

You already did this in Phase 2.1 (`docker compose up -d --build` on EC2) —
that's your manual sanity check. Confirm it's still running before moving
to Jenkins:

```bash
# On EC2:
docker compose ps
curl http://localhost:5000/health
```

Once confirmed, leave it running — Jenkins will simply `git pull` +
`docker compose up -d --build` again on top of it going forward, so there's
nothing to tear down manually.

---

## PHASE 5 — Jenkins Setup (on Windows)

### 5.1 Install required plugins
Jenkins → Manage Jenkins → Plugins → Available:
- **SSH Agent**
- **GitHub Integration**
- **Pipeline**

(No Docker plugin needed — Jenkins never runs Docker itself; it just SSHes
into EC2 and tells EC2's Docker what to do.)

### 5.2 Add credentials
Jenkins → Manage Jenkins → Credentials → System → Global credentials:

1. **EC2 SSH key** — Kind: SSH Username with private key, ID: `ec2-ssh-key-deployhub`,
   Username: `ubuntu`, Private key: paste contents of `deployhub-key.pem`

That's the only credential you need now — no Docker Hub account required.

### 5.3 Create the pipeline job
1. New Item → name `deployhub-pipeline` → **Pipeline**
2. Pipeline → Definition: **Pipeline script from SCM**
3. SCM: Git → Repo URL: your GitHub repo → Branch: `main`
4. Script Path: `Jenkinsfile`
5. Save

### 5.4 Edit the Jenkinsfile placeholders
In `Jenkinsfile`, replace:
- `YOUR_EC2_PUBLIC_IP` → your actual EC2 public IP (appears twice: `EC2_HOST` and `EC2_IP`)
- `<your-username>` in `REPO_URL` → your GitHub username

Make sure your EC2 instance already has the repo cloned once at
`~/deployhub` (you did this in Phase 2.1) — the pipeline runs `git pull`
there each time, falling back to a fresh clone if the folder is empty.

### 5.5 Expose local Jenkins to the internet (for GitHub webhook)
GitHub can't reach `localhost:8080` on your Windows machine directly, so
use **ngrok** as a tunnel:

```powershell
choco install ngrok        # or download from ngrok.com
ngrok http 8080
```
This gives you a public URL like `https://abcd1234.ngrok-free.app`.
Keep this terminal running whenever you want the webhook to work.

> Alternative: if ngrok's free-tier random URL resetting each restart annoys
> you, ngrok also offers a free static domain you can reserve once and reuse.

### 5.6 Configure the GitHub webhook
1. Your GitHub repo → Settings → Webhooks → **Add webhook**
2. Payload URL: `https://abcd1234.ngrok-free.app/github-webhook/`
3. Content type: `application/json`
4. Trigger: **Just the push event**
5. Save

### 5.7 Enable the trigger in Jenkins
Job → Configure → Build Triggers → check **GitHub hook trigger for GITScm polling** → Save

---

## PHASE 6 — Test the full pipeline end to end

1. Make a small change locally (e.g. edit `templates/index.html`)
2. `git add . && git commit -m "test webhook" && git push`
3. GitHub sends the webhook → ngrok forwards it → Jenkins job triggers automatically
4. Watch Jenkins **Console Output**:
   - Checkout ✅
   - Tests run (locally on Jenkins, Python only) ✅
   - SSH into EC2 → `git pull` → `docker compose up -d --build` (image built
     and container started using EC2's own Docker) ✅
   - Smoke test hits `/health` ✅
5. Visit `http://<EC2_PUBLIC_IP>:5000` — your change should be live within
   ~1-2 minutes of the git push, with zero manual steps.

---

## PHASE 7 — Demo script (for your resume/interview video)

1. Show the GitHub repo, then `git push` a small change live
2. Show Jenkins picking it up automatically (webhook, not manual trigger!)
3. Show the pipeline stages passing in the Jenkins console
4. Show the updated portal live on EC2
5. Now demonstrate the *actual product*: submit a sample repo URL into
   the DeployHub form itself, and show it clone → build → deploy → go live
   on a new port, all through your own automation code (not Jenkins this
   time — this is your app doing it)
6. Open the S3 bucket console and show the deployment log file that was
   just archived there
7. Show the IAM role attached to the EC2 instance — no access keys anywhere
   in your code, everything scoped through least-privilege permissions

---

## Common issues & fixes

| Problem | Fix |
|---|---|
| `docker.errors.DockerException: permission denied` on EC2 | Run `sudo usermod -aG docker ubuntu` then reconnect SSH session |
| Webhook not firing | Check ngrok tunnel is still running; ngrok free URLs expire on restart — update GitHub webhook URL if it changed |
| `ssh: connect to host ... port 22: Connection refused` from Jenkins | Check EC2 security group allows SSH (port 22) from your Jenkins machine's IP, and that `ec2-ssh-key` credential in Jenkins has the correct `.pem` contents |
| `docker compose up --build` fails with "permission denied" on EC2 | Re-run `sudo usermod -aG docker ubuntu && newgrp docker` on EC2, or reconnect the SSH session so group membership takes effect |
| Port already allocated | `allocate_port()` checks the DB but if a container crashed and left a stale row, delete the deployment record and retry |
| S3 upload silently fails | Check the IAM role JSON matches your actual bucket name exactly (typos are the #1 cause) |

---

## Extending this project further (optional, for extra marks)

- Add Nginx reverse proxy so deployed apps get `subdomain.yourdomain.com` instead of raw ports
- Add auto-cleanup: containers idle for 24h get auto-stopped (cost/resource control)
- Add CloudWatch metrics for container CPU/memory
- Add a "redeploy on git push" webhook *per deployed app* (full CI/CD for user apps too)
- Swap SQLite → RDS/DynamoDB for a "real" managed DB story
