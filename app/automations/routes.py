"""Views of the automations: list, visual/YAML editor, runs (traces), import and export."""

from __future__ import annotations

from flask import Response, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.automations import bp
from app.automations import engine
from app.automations.documents import apply, check_document, from_yaml, readable, to_yaml, unique_name
from app.automations.schema import ConfigError, editor_schema
from app.extensions import db
from app.i18n.catalog import gettext
from app.models import PLC, Alarm, Automation, AutomationRun, DataPoint, Perm
from app.models.automation import MODES
from app.utils.decorators import permission_required

manage = permission_required(Perm.MANAGE_AUTOMATIONS)


def _get(automation_id: int) -> Automation:
    """The automation or 404."""
    return db.get_or_404(Automation, automation_id)


def _errors(exc: ConfigError, status: int = 400):
    """JSON error response with the list of problems."""
    return jsonify(ok=False, errors=exc.errors), status


@bp.route("/")
@manage
def index():
    """List the automations with their state and last run.

    Returns:
        The rendered page.
    """
    q = (request.args.get("q") or "").strip().lower()
    items = Automation.query.order_by(Automation.name).all()
    if q:
        items = [a for a in items if q in a.name.lower() or q in (a.description or "").lower()]
    last = {}
    for a in items:
        last[a.id] = a.runs.first()
    return render_template("automations/index.html", automations=items, last=last, q=q,
                           running={a.id: engine.running_count(a.id) for a in items})


def _references() -> dict:
    """Tags, alarms, PLCs and automations for the editor's pickers."""
    tags = [{"id": dp.id, "label": dp.label, "plc": dp.plc.name if dp.plc else "", "type": dp.data_type,
             "unit": dp.unit or "", "writable": bool(dp.writable), "local": bool(dp.is_local), "bool": dp.is_bool,
             "numeric": dp.is_numeric,
             "true_text": dp.true_text, "false_text": dp.false_text}
            for dp in DataPoint.query.order_by(DataPoint.label).all()]
    return {"tags": tags,
            "alarms": [{"id": a.id, "label": a.name} for a in Alarm.query.order_by(Alarm.name)],
            "plcs": [{"id": p.id, "label": p.name} for p in PLC.query.order_by(PLC.name)],
            "automations": [{"id": a.id, "label": a.name} for a in Automation.query.order_by(Automation.name)]}


def _editor(automation: Automation | None, copy_of: Automation | None = None):
    """Render the editor for a new or existing automation (or a new copy of one)."""
    if copy_of is not None:
        doc = copy_of.to_document()
        doc["max_runs"] = copy_of.max_runs
        doc["name"] = unique_name(gettext("{0} (copy)", copy_of.name))
        allow = False
    elif automation is None:
        doc = {"name": "", "description": "", "enabled": True, "mode": "single", "max_runs": 10,
               "triggers": [], "conditions": [], "actions": []}
        allow = False
    else:
        doc = automation.to_document()
        doc["max_runs"] = automation.max_runs
        allow = automation.allow_writes
    return render_template("automations/edit.html", automation=automation, doc=doc, allow_writes=allow,
                           schema=editor_schema(), refs=_references(),
                           modes=[(k, gettext(v)) for k, v in MODES.items()], can_approve=current_user.is_admin)


@bp.route("/new")
@manage
def create():
    """Editor for a new automation (``?copy=<id>`` starts from a copy)."""
    source = db.session.get(Automation, request.args.get("copy", type=int) or 0)
    return _editor(None, copy_of=source)


@bp.route("/<int:automation_id>/edit")
@manage
def edit(automation_id: int):
    """Editor of an existing automation."""
    return _editor(_get(automation_id))


@bp.route("/save", methods=["POST"])
@manage
def save():
    """Create or update an automation from the editor.

    JSON body: ``{"id": null|id, "doc": {...}}`` (visual editor) or
    ``{"id": ..., "yaml": "..."}`` (text editor); ``allow_writes`` (administrators).

    Returns:
        JSON ``{"ok", "id", "url", "notes"}`` or ``{"ok": false, "errors"}`` (400).
    """
    body = request.get_json(silent=True) or {}
    automation = None
    if body.get("id"):
        automation = _get(int(body["id"]))
    try:
        doc = from_yaml(body["yaml"]) if "yaml" in body else body.get("doc")
        checked = check_document(doc, automation.id if automation else None)
    except ConfigError as exc:
        return _errors(exc)
    created = automation is None
    if created:
        automation = Automation()
        db.session.add(automation)
    notes = apply(automation, checked, current_user, body.get("allow_writes"))
    db.session.commit()
    if not automation.enabled:
        engine.stop_runs(automation.id)
    return jsonify(ok=True, id=automation.id, created=created, notes=notes,
                   url=url_for("automations.edit", automation_id=automation.id))


@bp.route("/api/to-yaml", methods=["POST"])
@manage
def api_to_yaml():
    """Convert the editor's document to YAML (references shown as names).

    Returns:
        JSON ``{"yaml"}``.
    """
    doc = (request.get_json(silent=True) or {}).get("doc") or {}
    try:
        doc = readable(doc)
    except Exception:  # noqa: BLE001 - half-filled editor content: show it as it is
        pass
    return jsonify(yaml=to_yaml(doc))


@bp.route("/api/from-yaml", methods=["POST"])
@manage
def api_from_yaml():
    """Check YAML text and return it as an editor document (references as ids).

    Returns:
        JSON ``{"ok", "doc"}`` or ``{"ok": false, "errors"}`` (400).
    """
    body = request.get_json(silent=True) or {}
    try:
        checked = check_document(from_yaml(body.get("yaml", "")), body.get("id") or None)
    except ConfigError as exc:
        return _errors(exc)
    doc = {k: checked[k] for k in ("name", "description", "enabled", "mode", "max_runs")}
    doc.update(checked["config"])
    return jsonify(ok=True, doc=doc)


@bp.route("/<int:automation_id>/toggle", methods=["POST"])
@manage
def toggle(automation_id: int):
    """Enable or disable an automation (disabling stops its runs)."""
    a = _get(automation_id)
    a.enabled = not a.enabled
    db.session.commit()
    if not a.enabled:
        engine.stop_runs(a.id)
    flash(gettext("Automation '{0}' enabled.", a.name) if a.enabled else gettext("Automation '{0}' disabled.", a.name),
          "success")
    return redirect(request.referrer or url_for("automations.index"))


@bp.route("/<int:automation_id>/run", methods=["POST"])
@manage
def run(automation_id: int):
    """Run the actions now (the triggers and conditions are skipped, like *Run actions* in Home Assistant)."""
    a = _get(automation_id)
    result = engine.trigger(a, gettext("Run manually by {0}", current_user.username), skip_conditions=True)
    if request.accept_mimetypes.best == "application/json" or request.is_json:
        return jsonify(ok=True, result=result)
    msg = {"started": gettext("Automation '{0}' started.", a.name),
           "restarted": gettext("Automation '{0}' restarted.", a.name),
           "queued": gettext("Automation '{0}' queued (it is still running).", a.name),
           "skipped": gettext("Automation '{0}' is already running.", a.name)}[result]
    flash(msg, "warning" if result == "skipped" else "success")
    return redirect(request.referrer or url_for("automations.index"))


@bp.route("/<int:automation_id>/stop", methods=["POST"])
@manage
def stop(automation_id: int):
    """Stop the running and queued runs of an automation."""
    a = _get(automation_id)
    n = engine.stop_runs(a.id)
    flash(gettext("{0} run(s) of '{1}' stopped.", n, a.name), "success")
    return redirect(request.referrer or url_for("automations.index"))


@bp.route("/<int:automation_id>/delete", methods=["POST"])
@manage
def delete(automation_id: int):
    """Delete an automation and its run history."""
    a = _get(automation_id)
    engine.stop_runs(a.id)
    name = a.name
    db.session.delete(a)
    db.session.commit()
    flash(gettext("Automation '{0}' deleted.", name), "success")
    return redirect(url_for("automations.index"))


@bp.route("/<int:automation_id>/runs")
@manage
def runs(automation_id: int):
    """Run history (traces) of an automation; ``?run=<id>`` selects one run."""
    a = _get(automation_id)
    items = a.runs.limit(engine.KEEP_RUNS + 5).all()
    selected = None
    if request.args.get("run", type=int):
        selected = AutomationRun.query.filter_by(id=request.args.get("run", type=int), automation_id=a.id).first()
    if selected is None and items:
        selected = items[0]
    return render_template("automations/runs.html", automation=a, runs=items, selected=selected,
                           running=engine.running_count(a.id))


@bp.route("/<int:automation_id>/export.yaml")
@manage
def export_one(automation_id: int):
    """Download one automation as YAML."""
    a = _get(automation_id)
    return _yaml_download([readable(a.to_document())], f"automation-{a.id}.yaml")


@bp.route("/export.yaml")
@manage
def export_all():
    """Download every automation as one YAML list."""
    return _yaml_download([readable(a.to_document()) for a in Automation.query.order_by(Automation.name)],
                          "automations.yaml")


def _yaml_download(docs: list, filename: str) -> Response:
    """YAML attachment."""
    head = "# pyPLCHMI automations – import them in Automations → Import\n"
    return Response(head + to_yaml(docs), mimetype="application/x-yaml",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.route("/import", methods=["POST"])
@manage
def import_file():
    """Import automations from a YAML file (one automation or a list).

    All or nothing: if any automation has an error, nothing is imported.
    Imported automations are not approved for writing tags.
    """
    storage = request.files.get("file")
    if not storage or not storage.filename:
        flash(gettext("Choose a YAML file."), "warning")
        return redirect(url_for("automations.index"))
    try:
        data = from_yaml(storage.read().decode("utf-8", errors="replace"))
    except ConfigError as exc:
        flash(gettext("{0}: {1}", storage.filename, "; ".join(exc.errors)), "danger")
        return redirect(url_for("automations.index"))
    docs = data if isinstance(data, list) else [data]
    problems, prepared = [], []
    taken: set = set()
    for i, doc in enumerate(docs, start=1):
        if isinstance(doc, dict) and doc.get("name"):
            base = str(doc["name"]).strip()[:90]
            name, n = unique_name(base), 1
            while name in taken:
                n += 1
                name = unique_name(f"{base} ({n})")
            taken.add(name)
            doc = dict(doc, name=name)
        try:
            prepared.append(check_document(doc))
        except ConfigError as exc:
            label = doc.get("name") if isinstance(doc, dict) else "?"
            problems.append(gettext("#{0} {1}: {2}", i, label, "; ".join(exc.errors)))
    if problems:
        flash(gettext("Nothing was imported: {0}", " | ".join(problems[:5])), "danger")
        return redirect(url_for("automations.index"))
    for checked in prepared:
        a = Automation()
        db.session.add(a)
        apply(a, checked, current_user, False)
    db.session.commit()
    flash(gettext("{0} automation(s) imported. Automations that write tags must be approved by an administrator.",
                  len(prepared)), "success")
    return redirect(url_for("automations.index"))
