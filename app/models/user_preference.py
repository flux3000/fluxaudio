"""
models/user_preference.py — Per-user key/value preference store.

Extensible without schema changes. New preferences are just new rows.

Known keys (MVP):
  ingest_file_behavior : "move" | "copy"
      Controls whether source folder is moved or copied into library on ingest.
      Default: "copy" (safer — preserves original until user is satisfied)
"""

from app.extensions import db


class UserPreference(db.Model):
    __tablename__ = "user_preference"

    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    key     = db.Column(db.String(64),  nullable=False)
    value   = db.Column(db.String(255), nullable=False)

    # Relationship
    user = db.relationship("User", back_populates="preferences")

    def __repr__(self):
        return f"<UserPreference user={self.user_id} {self.key}={self.value}>"
