# Import all models so SQLAlchemy can discover them when create_all() is called.
from .user import User, UserArtistPermission
from .user_preference import UserPreference
from .artist import Artist, ArtistAlias
from .performer import Performer, PerformerArtist, PerformerAlias
from .venue import Venue, VenueAlias
from .event import Event
from .performance import Performance
from .recording import Recording, RecordingFingerprint
from .recording_event import RecordingEvent
from .track import Track
from .play_log import PlayLog
