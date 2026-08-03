"""
Core orchestration logic for DeployHub.
Handles: git clone -> Dockerfile detection/generation -> image build ->
container run -> port allocation -> log capture -> S3 archival.
"""

import os
import shutil
import subprocess
import docker
import boto3

from models import db, Deployment

WORKDIR_ROOT = "/tmp/deployhub_builds"
PORT_RANGE_START = 9000
PORT_RANGE_END = 9100

S3_BUCKET = os.environ.get("DEPLOYHUB_S3_BUCKET", "deployhub-logs-bucket")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

docker_client = docker.from_env()
s3_client = boto3.client("s3", region_name=AWS_REGION)


# ---------- Helpers ----------

def _update(app, deploy_id, **fields):
    """Update a Deployment row from a background thread (needs app context)."""
    with app.app_context():
        d = Deployment.query.get(deploy_id)
        for key, value in fields.items():
            setattr(d, key, value)
        db.session.commit()


def _append_log(app, deploy_id, text):
    with app.app_context():
        d = Deployment.query.get(deploy_id)
        existing = d.logs_tail or ""
        combined = (existing + "\n" + text).strip()
        # keep only last ~200 lines to avoid bloating sqlite
        lines = combined.splitlines()[-200:]
        d.logs_tail = "\n".join(lines)
        db.session.commit()


def allocate_port(app):
    with app.app_context():
        used_ports = {d.port for d in Deployment.query.filter(Deployment.port.isnot(None)).all()}
    for port in range(PORT_RANGE_START, PORT_RANGE_END):
        if port not in used_ports:
            return port
    raise RuntimeError("No free ports available in range")


def detect_and_prepare_dockerfile(repo_path):
    """
    If the cloned repo already has a Dockerfile, use it.
    Otherwise, auto-generate a sensible one based on detected stack.
    Returns True if a Dockerfile is present/created.
    """
    dockerfile_path = os.path.join(repo_path, "Dockerfile")
    if os.path.exists(dockerfile_path):
        return True

    # Python project (Flask/FastAPI/Django etc.)
    if os.path.exists(os.path.join(repo_path, "requirements.txt")):
        content = (
            "FROM python:3.11-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install --no-cache-dir -r requirements.txt\n"
            "COPY . .\n"
            "EXPOSE 5000\n"
            "CMD [\"python\", \"app.py\"]\n"
        )
        with open(dockerfile_path, "w") as f:
            f.write(content)
        return True

    # Node.js project
    if os.path.exists(os.path.join(repo_path, "package.json")):
        content = (
            "FROM node:20-slim\n"
            "WORKDIR /app\n"
            "COPY package*.json ./\n"
            "RUN npm install\n"
            "COPY . .\n"
            "EXPOSE 3000\n"
            "CMD [\"npm\", \"start\"]\n"
        )
        with open(dockerfile_path, "w") as f:
            f.write(content)
        return True

    return False  # unsupported stack, can't auto-generate


def upload_logs_to_s3(deploy_id, log_text):
    key = f"deployment-logs/{deploy_id}.log"
    try:
        s3_client.put_object(Bucket=S3_BUCKET, Key=key, Body=log_text.encode("utf-8"))
        return key
    except Exception as e:
        print(f"[WARN] Failed to upload logs to S3: {e}")
        return None


# ---------- Main pipeline ----------

def run_deployment_pipeline(app, deploy_id, repo_url, branch):
    repo_path = os.path.join(WORKDIR_ROOT, deploy_id)
    full_log = []

    try:
        # 1. CLONE
        _update(app, deploy_id, status="CLONING")
        os.makedirs(WORKDIR_ROOT, exist_ok=True)
        clone_cmd = ["git", "clone", "--depth", "1", "--branch", branch, repo_url, repo_path]
        result = subprocess.run(clone_cmd, capture_output=True, text=True, timeout=120)
        full_log.append(result.stdout + result.stderr)
        _append_log(app, deploy_id, result.stdout + result.stderr)

        if result.returncode != 0:
            raise RuntimeError("git clone failed - check repo URL/branch")

        # 2. DETECT / GENERATE DOCKERFILE
        if not detect_and_prepare_dockerfile(repo_path):
            raise RuntimeError("No Dockerfile found and stack not auto-detected (only Python/Node supported)")

        # 3. BUILD IMAGE
        _update(app, deploy_id, status="BUILDING")
        image_tag = f"deployhub/{deploy_id}:latest"
        image, build_logs = docker_client.images.build(path=repo_path, tag=image_tag, rm=True)

        build_log_text = ""
        for chunk in build_logs:
            if "stream" in chunk:
                build_log_text += chunk["stream"]
        full_log.append(build_log_text)
        _append_log(app, deploy_id, build_log_text[-2000:])  # avoid huge writes

        # 4. RUN CONTAINER
        port = allocate_port(app)
        # NOTE: we map host_port -> container port 5000 (Python default) or 3000 (Node)
        internal_port = 3000 if os.path.exists(os.path.join(repo_path, "package.json")) else 5000

        container = docker_client.containers.run(
            image_tag,
            detach=True,
            ports={f"{internal_port}/tcp": port},
            name=f"deployhub-{deploy_id}",
            restart_policy={"Name": "unless-stopped"},
        )

        _update(
            app,
            deploy_id,
            status="RUNNING",
            image_tag=image_tag,
            container_id=container.id,
            port=port,
        )
        full_log.append(f"Container running on host port {port}")
        _append_log(app, deploy_id, f"Deployed successfully on port {port}")

    except Exception as e:
        error_text = f"DEPLOYMENT FAILED: {str(e)}"
        full_log.append(error_text)
        _append_log(app, deploy_id, error_text)
        _update(app, deploy_id, status="FAILED")

    finally:
        # Archive full logs to S3 regardless of outcome
        s3_key = upload_logs_to_s3(deploy_id, "\n".join(full_log))
        if s3_key:
            _update(app, deploy_id, logs_s3_key=s3_key)
        # Clean up cloned source (image already built, don't need source anymore)
        shutil.rmtree(repo_path, ignore_errors=True)


# ---------- Container lifecycle ----------

def stop_container(container_id):
    if not container_id:
        return
    try:
        c = docker_client.containers.get(container_id)
        c.stop()
    except docker.errors.NotFound:
        pass


def remove_container(container_id):
    if not container_id:
        return
    try:
        c = docker_client.containers.get(container_id)
        c.stop()
        c.remove()
    except docker.errors.NotFound:
        pass
