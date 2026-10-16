"""WTForms class for the tag table."""

from wtforms import BooleanField, IntegerField
from wtforms.validators import NumberRange, Optional

from app.admin.forms import MultiCheckboxField
from app.dashboards.forms import DataPointForm
from app.history.logger import MAX_INTERVAL, MIN_INTERVAL
from app.i18n.catalog import gettext
from app.models import Dashboard, DataPoint
from app.plc.dummy import DummyAddress


class TagForm(DataPointForm):
    """A tag form with the screens that show the tag (any number, or none) and the history settings."""

    screens = MultiCheckboxField("Screens", coerce=int,
                                 description="Optional – tags on no screen can still be used by alarms.")
    log_enabled = BooleanField("Save history (samples for trends and Excel export)")
    log_interval = IntegerField("Sample every (seconds)", default=60,
                                validators=[Optional(), NumberRange(min=MIN_INTERVAL, max=MAX_INTERVAL)],
                                description="1 to 86400 seconds. Each sample is saved with the full date and time.")

    def __init__(self, *args, **kwargs):
        """Build the form and list the screens.

        Args:
            *args: Passed to :class:`DataPointForm`.
            **kwargs: Passed to :class:`DataPointForm`.
        """
        super().__init__(*args, **kwargs)
        self.screens.choices = [(d.id, d.name) for d in Dashboard.query.order_by(Dashboard.name)]
        if self.screens.data is None:
            self.screens.data = []

    def validate(self, extra_validators=None) -> bool:
        """Run the tag checks, then the history checks.

        Local dummy tags have no value on the server, so they cannot be logged.

        Args:
            extra_validators: Passed to :meth:`DataPointForm.validate`.

        Returns:
            ``True`` if the whole form is valid.
        """
        if not super().validate(extra_validators=extra_validators):
            return False
        if self.log_enabled.data:
            if isinstance(self.parsed_address, DummyAddress) and self.dummy_scope.data == "local":
                self.log_enabled.errors = list(self.log_enabled.errors) + [
                    gettext("A local dummy tag has no value on the server, so its history cannot be saved.")]
                return False
            if self.log_interval.data is None:
                self.log_interval.errors = list(self.log_interval.errors) + [gettext("Enter the sample interval.")]
                return False
        return True

    def populate_datapoint(self, dp: DataPoint) -> None:
        """Copy the validated form into a tag, including the history settings.

        Args:
            dp: The tag to update.
        """
        super().populate_datapoint(dp)
        dp.log_enabled = bool(self.log_enabled.data) and not dp.is_local
        if self.log_interval.data is not None:
            dp.log_interval = int(self.log_interval.data)
        elif dp.log_interval is None:
            dp.log_interval = 60

    def load_datapoint(self, dp: DataPoint) -> None:
        """Pre-fill the form from a tag, including the history settings.

        Args:
            dp: The tag.
        """
        super().load_datapoint(dp)
        self.log_enabled.data = bool(dp.log_enabled)
        self.log_interval.data = dp.log_interval or 60
