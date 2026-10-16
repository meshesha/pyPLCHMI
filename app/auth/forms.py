"""WTForms classes for the authentication blueprint."""

from app.i18n.forms import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Length


class LoginForm(FlaskForm):
    """Username/password login form."""

    username = StringField("Username", validators=[DataRequired(), Length(max=64)])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Keep me logged in")
    submit = SubmitField("Log in")


class ChangePasswordForm(FlaskForm):
    """Form allowing a logged-in user to change their own password."""

    current_password = PasswordField("Current password", validators=[DataRequired()])
    new_password = PasswordField("New password", validators=[DataRequired(), Length(min=8, max=128)])
    confirm = PasswordField(
        "Repeat new password", validators=[DataRequired(), EqualTo("new_password", message="Passwords must match")]
    )
    submit = SubmitField("Change password")
