# Import all models so SQLAlchemy can discover them when create_all() is called.
from .user import User, UserArtistPermission
from .user_preference import UserPreference
from .performer import Performer
from .artist import Artist, Membership
from .venue import Venue
from .event import Event
from .performance import Performance
from .recording import Recording, RecordingFingerprint
from .collection import Collection, CollectionRecording
from .recording_event import RecordingEvent
from .track import Track
from .play_log import PlayLog
