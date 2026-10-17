"""Tests for user, role and PLC administration."""

from app.models import PLC, DataPoint, Perm, Role, User


class TestUsers:
    """User management."""

    def test_create_user(self, client, login_as):
        """Admins can create users."""
        login_as("admin")
        role = Role.query.filter_by(name="Guest").one()
        resp = client.post("/admin/users/new", data={
            "username": "op1", "email": "op1@example.com", "role_id": role.id, "active": "y",
            "password": "operator-pass", "confirm": "operator-pass"})
        assert resp.status_code == 302
        user = User.query.filter_by(username="op1").one()
        assert user.role.name == "Guest" and user.check_password("operator-pass")

    def test_create_requires_password(self, client, login_as):
        """New users must get a password."""
        login_as("admin")
        role = Role.query.first()
        resp = client.post("/admin/users/new", data={"username": "op2", "role_id": role.id})
        assert b"password is required" in resp.data

    def test_duplicate_username(self, client, login_as):
        """Usernames must be unique."""
        login_as("admin")
        role = Role.query.first()
        resp = client.post("/admin/users/new", data={
            "username": "viewer", "role_id": role.id, "password": "abcdefgh", "confirm": "abcdefgh"})
        assert b"already in use" in resp.data

    def test_edit_user_keeps_password(self, client, login_as, users):
        """Editing without a password keeps the old one."""
        login_as("admin")
        guest_role = Role.query.filter_by(name="Guest").one()
        uid = users["viewer"].id
        client.post(f"/admin/users/{uid}/edit", data={"username": "viewer", "role_id": guest_role.id, "active": "y"})
        user = User.query.get(uid)
        assert user.role.name == "Guest"
        assert user.check_password("secret-pass-123")

    def test_cannot_demote_last_admin(self, client, login_as, users):
        """The last administrator cannot lose admin rights."""
        login_as("admin")
        viewer_role = Role.query.filter_by(name="Viewer").one()
        resp = client.post(f"/admin/users/{users['admin'].id}/edit",
                           data={"username": "admin", "role_id": viewer_role.id, "active": "y"})
        assert b"last administrator" in resp.data
        assert User.query.get(users["admin"].id).is_admin

    def test_cannot_delete_self(self, client, login_as, users):
        """Admins cannot delete their own account."""
        login_as("admin")
        client.post(f"/admin/users/{users['admin'].id}/delete")
        assert User.query.get(users["admin"].id) is not None

    def test_delete_user(self, client, login_as, users):
        """Other users can be deleted."""
        login_as("admin")
        uid = users["guest"].id
        client.post(f"/admin/users/{uid}/delete")
        assert User.query.get(uid) is None


class TestRoles:
    """Role/permission matrix."""

    def test_save_matrix(self, client, login_as):
        """Posting the matrix updates role permissions."""
        login_as("admin")
        viewer = Role.query.filter_by(name="Viewer").one()
        admin = Role.query.filter_by(name="Admin").one()
        client.post("/admin/roles", data={
            f"perm-{viewer.id}": [Perm.VIEW_SCREENS, Perm.WRITE_VALUES],
            f"perm-{admin.id}": [Perm.MANAGE_USERS],
        })
        assert viewer.permission_codes == {Perm.VIEW_SCREENS, Perm.WRITE_VALUES}
        # Admin keeps manage_roles even if unticked (lock-out protection)
        assert admin.has_permission(Perm.MANAGE_ROLES)

    def test_create_and_delete_role(self, client, login_as):
        """Custom roles can be added and removed."""
        login_as("admin")
        client.post("/admin/roles/new", data={"name": "Shift lead", "description": "Shift lead"})
        role = Role.query.filter_by(name="Shift lead").one()
        client.post(f"/admin/roles/{role.id}/delete")
        assert Role.query.filter_by(name="Shift lead").first() is None

    def test_default_role_protected(self, client, login_as):
        """Default roles cannot be deleted."""
        login_as("admin")
        guest = Role.query.filter_by(name="Guest").one()
        client.post(f"/admin/roles/{guest.id}/delete")
        assert Role.query.filter_by(name="Guest").first() is not None


class TestPLCs:
    """PLC management."""

    def test_create_plc(self, client, login_as):
        """A PLC can be added."""
        login_as("admin")
        resp = client.post("/admin/plcs/new", data={
            "name": "Line1", "ip_address": "192.168.0.10", "rack": 0, "slot": 1, "tcp_port": 102, "enabled": "y"})
        assert resp.status_code == 302
        assert PLC.query.filter_by(name="Line1").one().slot == 1

    def test_invalid_ip(self, client, login_as):
        """Invalid IP/host names are rejected."""
        login_as("admin")
        resp = client.post("/admin/plcs/new", data={
            "name": "Bad", "ip_address": "300.1.1.1 x", "rack": 0, "slot": 1, "tcp_port": 102})
        assert b"valid IPv4" in resp.data

    def test_duplicate_name(self, client, login_as, plc):
        """PLC names are unique."""
        login_as("admin")
        resp = client.post("/admin/plcs/new", data={
            "name": "PLC1", "ip_address": "10.0.0.9", "rack": 0, "slot": 1, "tcp_port": 102})
        assert b"already exists" in resp.data

    def test_edit_plc(self, client, login_as, plc):
        """PLC settings can be edited."""
        login_as("admin")
        client.post(f"/admin/plcs/{plc.id}/edit", data={
            "name": "PLC1", "ip_address": "10.0.0.2", "rack": 0, "slot": 2, "tcp_port": 102, "enabled": "y"})
        assert PLC.query.get(plc.id).ip_address == "10.0.0.2"

    def test_delete_plc_cascades(self, client, login_as, dashboards, plc):
        """Deleting a PLC removes its data points."""
        login_as("admin")
        client.post(f"/admin/plcs/{plc.id}/delete")
        assert PLC.query.count() == 0 and DataPoint.query.count() == 0

    def test_test_connection(self, client, login_as, plc):
        """The test button reports success in simulation."""
        login_as("admin")
        resp = client.post(f"/admin/plcs/{plc.id}/test", follow_redirects=True)
        assert b"Connected to" in resp.data
