"""Custom ``flask`` command-line commands.

Available after ``create_app`` registers them::

    flask seed                     # create default roles & permissions
    flask create-admin             # interactive admin account creation
    flask create-admin --username admin --password ... --if-missing   # non-interactive (Docker)
    flask demo                     # sample PLC + dashboard (best with PLC_SIMULATION=1)
    flask library animations       # add the built-in animated GIFs to the image library
    flask library folders          # list the image library folders
"""

import click
from flask import Flask

from app.extensions import db
from app.models import PLC, Alarm, Dashboard, DataPoint, Role, User
from app.plc.addressing import parse_address


def register_cli(app: Flask) -> None:
    """Register the custom CLI commands on ``app``.

    Args:
        app: The Flask application.
    """
    app.cli.add_command(seed_command)
    app.cli.add_command(create_admin_command)
    app.cli.add_command(demo_command)
    app.cli.add_command(library_group)


@click.command("seed")
@click.option("--reset", is_flag=True, help="Restore factory permissions on existing roles.")
def seed_command(reset: bool) -> None:
    """Create/refresh the default roles (Admin, Viewer, Guest) and permissions.

    Args:
        reset: When given, existing roles get their default permissions back.
    """
    Role.insert_defaults(reset_permissions=reset)
    click.echo("Roles and permissions are up to date.")


@click.command("create-admin")
@click.option("--username", prompt=True, help="Login name.")
@click.option("--email", default="", help="Optional e-mail address.")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True, help="Password.")
@click.option("--if-missing", is_flag=True,
              help="Do nothing (exit code 0) when the user already exists – for container start scripts.")
def create_admin_command(username: str, email: str, password: str, if_missing: bool) -> None:
    """Create a user with the Admin role.

    Args:
        username: Login name (must be unique).
        email: Optional e-mail address.
        password: Plain-text password (stored as a bcrypt hash).
        if_missing: Skip quietly if the user exists (the password is not changed).

    Raises:
        click.ClickException: If the user exists (without ``--if-missing``) or
            the password is shorter than 8 characters.
    """
    Role.insert_defaults()
    if User.query.filter_by(username=username).first():
        if if_missing:
            click.echo(f"User '{username}' already exists – not changed.")
            return
        raise click.ClickException(f"User '{username}' already exists.")
    if len(password) < 8:
        raise click.ClickException("Password must be at least 8 characters.")
    admin_role = Role.query.filter_by(name="Admin").one()
    db.session.add(User(username=username, email=email or None, password=password, role=admin_role))
    db.session.commit()
    click.echo(f"Admin user '{username}' created.")


@click.command("demo")
def demo_command() -> None:
    """Create a demo PLC and an example dashboard with several data points.

    Intended for trying the application with ``PLC_SIMULATION=1``.
    """
    Role.insert_defaults()
    plc = PLC.query.filter_by(name="DemoPLC").first()
    if plc is None:
        plc = PLC(name="DemoPLC", ip_address="192.168.0.10", rack=0, slot=1, description="Demo S7-1200")
        db.session.add(plc)
        db.session.flush()
        Alarm.sync_comm_alarm(plc, True, "high")
    if Dashboard.query.filter_by(slug="demo-overview").first():
        click.echo("Demo already present.")
        return
    dash = Dashboard(name="Demo overview", slug="demo-overview", is_public=True, refresh_ms=1000,
                     description="Example screen created by `flask demo`.")
    db.session.add(dash)
    specs = [
        ("Tank temperature", "DB1.DBD0:REAL", "gauge", dict(unit="°C", min_value=0, max_value=100, decimals=1)),
        ("Pressure", "DB1.DBD4:REAL", "value", dict(unit="bar", decimals=2)),
        ("Motor speed", "DB1.DBW8:INT", "gauge", dict(unit="rpm", min_value=0, max_value=1000, decimals=0)),
        ("Pump running", "DB1.DBX10.0", "indicator", dict(true_text="RUNNING", false_text="STOPPED")),
        ("Pump enable", "M0.0", "switch", dict(writable=True)),
        ("Setpoint", "DB1.DBD12:REAL", "value", dict(unit="°C", writable=True, decimals=1)),
        ("Batch name", "DB1.DBB20:STRING[20]", "value", dict(writable=True)),
        ("Input I0.0", "I0.0", "indicator", {}),
    ]
    for pos, (label, addr, widget, extra) in enumerate(specs):
        dp = DataPoint(plc=plc, label=label, widget=widget, **extra)
        dp.set_address(parse_address(addr))
        db.session.add(dp)
        dash.add_tag(dp, pos)
    db.session.commit()
    click.echo("Demo PLC and screen created (open /screens/demo-overview).")


@click.group("library")
def library_group():
    """Image library commands."""


@library_group.command("animations")
def library_animations_command():
    """Add the built-in animated GIFs to the image library (folders Animations/<Category>).

    Animations that are already in the library are skipped, so the command
    can be run again after an update.
    """
    from app.assets.folders import import_builtin_animations  # noqa: PLC0415

    result = import_builtin_animations()
    db.session.commit()
    click.echo(f"{result['added']} animation(s) added, {result['skipped']} already in the library"
               + (f", {result['missing']} file(s) missing" if result["missing"] else "") + ".")
    for folder in result["folders"]:
        click.echo(f"  {folder}")


@library_group.command("folders")
def library_folders_command():
    """Print the image library folders with their number of images."""
    from app.assets.folders import folder_tree  # noqa: PLC0415

    for node in folder_tree():
        click.echo(f"{'  ' * node['depth']}{node['name']}  ({node['count']} / {node['total']})")
