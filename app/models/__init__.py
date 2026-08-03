# Import all models so SQLAlchemy can discover them when create_all() is called.
from .user import User, UserArtistPermission
from .user_preference import UserPreference
from .genre import Genre
from .performer import Performer, PerformerResource
from .artist import Artist, Membership
from .venue import Venue
from .event import Event
from .performance import Performance
from .performance_personnel import PerformancePersonnel
from .recording import Recording, RecordingFingerprint
from .collection import Collection, CollectionRecording
from .peer import Peer, CollectionGrant, PeerInvite, PeerToken, PeerAccessLog
from .recording_event import RecordingEvent
from .track import Track
from .play_log import PlayLog
from .quality import QualityAnalysis, RecordingQuality
