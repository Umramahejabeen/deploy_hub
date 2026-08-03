"""
DeployHub - Self-Service Mini Deployment Portal
Main Flask application: routes + orchestration entrypoint.
"""

import os
import threading
import uuid
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

from models import db, Deployment
import deploy_manager as dm

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-prod")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///deployhub.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

with app.app_context():
    db.create_all()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/deploy", methods=["POST"])
def deploy():
    repo_url = request.form.get("repo_url", "").strip()
    branch = request.form.get("branch", "main").strip() or "main"

    if not repo_url.startswith("https://github.com/"):
        flash("Please enter a valid GitHub repo URL (https://github.com/user/repo).", "danger")
        return redirect(url_for("index"))

    deploy_id = str(uuid.uuid4())[:8]

    record = Deployment(
        id=deploy_id,
        repo_url=repo_url,
        branch=branch,
        status="QUEUED",
        created_at=datetime.utcnow(),
    )
    db.session.add(record)
    db.session.commit()

    # Run the heavy lifting (clone, build, run) in a background thread
    # so the HTTP request returns immediately.
    thread = threading.Thread(
        target=dm.run_deployment_pipeline,
        args=(app, deploy_id, repo_url, branch),
        daemon=True,
    )
    thread.start()

    flash(f"Deployment {deploy_id} queued! Track its progress on the dashboard.", "success")
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    deployments = Deployment.query.order_by(Deployment.created_at.desc()).all()
    return render_template("dashboard.html", deployments=deployments)


@app.route("/api/status/<deploy_id>")
def api_status(deploy_id):
    d = Deployment.query.get_or_404(deploy_id)
    return jsonify(
        {
            "id": d.id,
            "status": d.status,
            "port": d.port,
            "logs_tail": d.logs_tail,
        }
    )


@app.route("/stop/<deploy_id>", methods=["POST"])
def stop(deploy_id):
    d = Deployment.query.get_or_404(deploy_id)
    dm.stop_container(d.container_id)
    d.status = "STOPPED"
    db.session.commit()
    flash(f"Deployment {deploy_id} stopped.", "info")
    return redirect(url_for("dashboard"))


@app.route("/delete/<deploy_id>", methods=["POST"])
def delete(deploy_id):
    d = Deployment.query.get_or_404(deploy_id)
    dm.remove_container(d.container_id)
    db.session.delete(d)
    db.session.commit()
    flash(f"Deployment {deploy_id} removed.", "info")
    return redirect(url_for("dashboard"))


@app.route("/health")
def health():
    # Used by Docker HEALTHCHECK and Jenkins post-deploy smoke test
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
