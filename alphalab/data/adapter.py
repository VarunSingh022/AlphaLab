"""Adapter isolating provider logic to Canonical Dataset inputs."""

from alphalab.data.exceptions import DataValidationError
from alphalab.data.metadata import DatasetMetadata
from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import TimeFrequency

__all__ = ["DataAdapter"]


class DataAdapter:
    """Stateless generator for Data Engine Metadata."""

    @staticmethod
    def create_metadata(
        ds_id: str, src: str, asset: str, freq: str, start: float, end: float
    ) -> DatasetMetadata:
        """Map provider strings onto the canonical enums, refusing what it cannot.

        Until v3.1 an unrecognised asset class silently became ``EQUITY`` and an
        unrecognised frequency silently became ``DAILY``. A vendor file of
        options quotes labelled ``"opt"`` was therefore catalogued as equities,
        and a minute series labelled ``"1min"`` as daily bars -- in both cases
        producing a dataset that looked correct and was mis-classified, with
        nothing anywhere recording that a substitution had happened.

        Both now refuse and name the accepted spellings. This is the same rule
        ADR-0019 applies to currency and ADR-0020 to rates: a wrong value here
        produces a plausible dataset rather than an error, which is what makes
        the default worse than the refusal.

        Raises:
            DataValidationError: If ``asset`` or ``freq`` names no member.
        """

        try:
            asset_class = DataAssetClass[asset.strip().upper()]
        except KeyError:
            raise DataValidationError(
                f"{ds_id}: {asset!r} is not an asset class. Accepted: "
                f"{', '.join(member.name for member in DataAssetClass)}."
            ) from None

        try:
            time_freq = TimeFrequency[freq.strip().upper()]
        except KeyError:
            raise DataValidationError(
                f"{ds_id}: {freq!r} is not a frequency. Accepted: "
                f"{', '.join(member.name for member in TimeFrequency)}."
            ) from None

        return DatasetMetadata(ds_id, src, asset_class, time_freq, start, end)
