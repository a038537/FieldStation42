from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional


def _sorted_paths(paths: Iterable[str]) -> List[str]:
    """Return a list of paths sorted alpha-numerically.

    The test-suite expects deterministic ordering of episodes.  We therefore
    normalise any provided iterable into a list and sort it.  The helper lives
    at module scope to keep :class:`SeriesIndex` focused on state management.
    """

    return sorted(str(p) for p in paths)


@dataclass
class SeriesIndex:
    """Simple iterator over a series of episode file paths.

    The class keeps track of the current position and provides helpers for
    generating a unique key used by the database layer.  It intentionally keeps
    a tiny footprint – just enough behaviour for the tests and the broader
    application to reason about sequences of episodes.
    """

    series_name: str
    episodes: List[str] = field(default_factory=list)
    _index: int = 0

    @staticmethod
    def make_key(series_name: str, sequence_name: str) -> str:
        """Create the composite key used for lookups.

        >>> SeriesIndex.make_key("show", "intro")
        'show-intro'
        """

        return f"{series_name}-{sequence_name}"

    # ------------------------------------------------------------------
    # population & bookkeeping
    def populate(self, file_list: Iterable[str]) -> None:
        """Populate the index with a collection of file paths.

        The order is normalised so callers get a deterministic traversal
        irrespective of the input order.
        """

        self.episodes = _sorted_paths(file_list)
        self._index = 0

    def get_series_length(self) -> int:
        """Return the number of episodes currently indexed."""

        return len(self.episodes)

    # ------------------------------------------------------------------
    # iteration
    def get_next(self) -> Optional[str]:
        """Return the next episode path, cycling when the end is reached."""

        if not self.episodes:
            return None

        episode = self.episodes[self._index]
        self._index = (self._index + 1) % len(self.episodes)
        return episode
