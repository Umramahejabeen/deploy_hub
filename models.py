from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Deployment(db.Model):
    __tablename__ = "deployments"

    id = db.Column(db.String(16), primary_key=True)
    repo_url = db.Column(db.String(255), nullable=False)
    branch = db.Column(db.String(100), default="main")
    status = db.Column(db.String(30), default="QUEUED")
    # QUEUED -> CLONING -> BUILDING -> RUNNING -> FAILED/STOPPED

    image_tag = db.Column(db.String(255))
    container_id = db.Column(db.String(100))
    port = db.Column(db.Integer)

    logs_tail = db.Column(db.Text)          # last ~50 lines shown on dashboard
    logs_s3_key = db.Column(db.String(255))  # full logs archived to S3

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
