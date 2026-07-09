"""
models/play_log.py — Play log model.

Every track play by every user is recorded as a separate row.
Full history is retained (not aggregated) to support:
  - Usage statistics and preference profiling
  - Future royalty reporting (who played what, when, for how long)
"""

from datetime import datetime, timezone
from app.extensions import db


class PlayLog(db.Model):
    __tablename__ = "play_log"

    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey("user.id"),  nullable=False)
    track_id        = db.Column(db.Integer, db.ForeignKey("track.id"), nullable=False)

    played_at       = db.Column(db.DateTime, nullable=False,
                                default=lambda: datetime.now(timezone.utc))

    # Seconds of audio actually heard (enables skip detection)
    duration_played = db.Column(db.Integer, nullable=True)

    # True when playback reached the end of the track
    completed       = db.Column(db.Boolean, nullable=False, default=False)

    # Relationships
    user  = db.relationship("User",  back_populates="play_logs")
    track = db.relationship("Track", back_populates="play_logs")

    def __repr__(self):
        return f"<PlayLog user={self.user_id} track={self.track_id} @ {self.played_at}>"
