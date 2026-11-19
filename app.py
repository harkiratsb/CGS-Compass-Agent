"""
CGS Process Discovery — Stage 1: Ingestion & Current-State Mapping

Run:  streamlit run app.py
Modes: live extraction when ANTHROPIC_API_KEY is set (see .env.example),
       cached extraction for the shipped demo materials otherwise.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# load .env if present (no dependency on python-dotenv)
_env = Path(__file__).with_name(".env")
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from schema import ProcessModel                       # noqa: E402
from ingest.parse import parse_file                   # noqa: E402
from project import ProjectError, load_project, parsed_files, save_project  # noqa: E402
from ingest import drive                              # noqa: E402
from ingest.agent import run_agent                   # noqa: E402
from ingest.merge import dedupe_activities, source_coverage  # noqa: E402
from render.swimlane import build_geometry, render_svg, render_png  # noqa: E402
from render.drawio import drawio_url, render_drawio    # noqa: E402
from ingest.drawio_import import import_drawio        # noqa: E402
from cgs_tools import tags_for                        # noqa: E402
from handoff import build_handoff, check_handoff, speaker_names, with_nicknames  # noqa: E402
from framework.models import FrameworkRun                # noqa: E402
from phase3_ui import render_phase3                      # noqa: E402

ORANGE = "#FF6428"
DEMO_DIR = Path(__file__).with_name("demo_data")

st.set_page_config(page_title="CGS Process Discovery — Stage 1", page_icon="🧭", layout="wide")
st.markdown(f"""
<style>
  .cgs-header {{display:flex;align-items:center;gap:14px;margin-bottom:4px}}
  .cgs-logo {{background:{ORANGE};color:#fff;font-weight:800;padding:8px 12px;border-radius:6px 6px 6px 0;
              font-family:Inter,Helvetica,Arial;line-height:1;font-size:18px}}
  .cgs-logo small {{display:block;font-size:9px;font-weight:600;letter-spacing:.08em}}
  .cgs-title {{font-size:22px;font-weight:700;margin:0}}
  .cgs-sub {{color:#6B7280;margin:0;font-size:13px}}
  .stage {{display:inline-block;padding:2px 10px;border-radius:999px;background:#FFF0E8;color:{ORANGE};
           font-weight:600;font-size:12px;border:1px solid {ORANGE}}}
  .mode {{display:inline-block;padding:2px 10px;border-radius:999px;background:#EEF2F7;color:#374151;font-size:12px}}
  div[data-testid="stTabs"] button[aria-selected="true"] {{color:{ORANGE};border-bottom-color:{ORANGE}}}
  .stButton>button[kind="primary"] {{background:{ORANGE};border-color:{ORANGE}}}
</style>
<div class="cgs-header">
  <div class="cgs-logo">CGS<small>ADVISORS</small></div>
  <div>
    <p class="cgs-title">Process Discovery &nbsp;<span class="stage">Stage 1 · Ingestion &amp; Current-State Mapping</span></p>
    <p class="cgs-sub">Transcript Synthesizer → Knowledge Ingestion → Process Drafter</p>
  </div>
</div>
""", unsafe_allow_html=True)

ss = st.session_state
ss.setdefault("files", [])          # list of (name, bytes)
ss.setdefault("parsed", [])
ss.setdefault("model", None)
ss.setdefault("mode", None)
ss.setdefault("run", None)
ss.setdefault("drive_files", [])
ss.setdefault("map_reviewed", False)
# from map_reviewed, so the toggle comes back right after the Stage 1 tabs were not drawn (Phase 3 view)
ss.setdefault("map_reviewed_toggle", ss.map_reviewed)
ss.setdefault("framework", None)
if ss.pop("_reset_reviewed", False):   # the map changed after the toggle was drawn
    ss.map_reviewed = ss["map_reviewed_toggle"] = False


def _reset_review_state():
    ss.run, ss.framework, ss.map_reviewed = None, None, False
    ss["map_reviewed_toggle"] = False

INTAKE_DEFAULTS = {
    "process_name": "CGS Lead & Opportunity Management",
    "process_owner": "Director of Operations",
    "process_scope": "Lead identification through alignment on a discussion document. "
                     "Proposal/SOW is a separate process.",
    "account_id": "ACC-0001",
    "process_id": "P-01",
    "function_ref": "",
    "extra_names": "",
}
for _k, _v in INTAKE_DEFAULTS.items():
    ss.setdefault(_k, _v)
DEFINITION_STATUSES = ["draft", "reviewed", "approved"]
ss.setdefault("definition_status", "draft")
INTAKE_KEYS = ["process_name", "process_owner", "process_scope", "account_id", "process_id",
               "function_ref", "definition_status", "extra_names"]

# ---------------------------------------------------------------- sidebar: intake
with st.sidebar:
    with st.expander("Open project"):
        proj_up = st.file_uploader("Open a saved project (.json)", type=["json"], key="project_upload")
        if proj_up is not None and ss.get("_opened_project") != (proj_up.name, proj_up.size):
            try:
                proj, proj_files = load_project(proj_up.getvalue().decode("utf-8"))
            except (ProjectError, UnicodeDecodeError) as e:
                st.error(str(e) if isinstance(e, ProjectError) else "This file is not a valid project file.")
            else:
                ss._opened_project = (proj_up.name, proj_up.size)
                ss.files, ss.parsed = proj_files, parsed_files(proj_files)
                ss.model, ss.run, ss.mode = proj.model, proj.run, proj.mode
                ss.map_reviewed = ss["map_reviewed_toggle"] = proj.map_reviewed
                try:
                    ss.framework = FrameworkRun.model_validate(proj.framework) if proj.framework else None
                except ValueError:
                    ss.framework = None
                    st.warning("The saved framework run couldn't be read; run the framework again.")
                for k in INTAKE_KEYS:
                    if k in proj.intake and (k != "definition_status" or proj.intake[k] in DEFINITION_STATUSES):
                        ss[k] = proj.intake[k]
                st.success(f"Opened project ({len(proj_files)} file(s)).")
    st.subheader("1 · Process intake")
    process_name = st.text_input("Process name", key="process_name")
    process_owner = st.text_input("Process owner", key="process_owner")
    process_scope = st.text_area("Scope / context for the agent", height=80, key="process_scope")
    with st.expander("Handoff details (for opportunity mapping)"):
        account_id = st.text_input("Account ID", key="account_id")
        process_id = st.text_input("Process ID", key="process_id")
        function_ref = st.text_input("Function ref", key="function_ref", help="CGS function reference, e.g. 3.1")
        definition_status = st.selectbox("Definition status", key="definition_status", options=DEFINITION_STATUSES)
        extra_names = st.text_input("Other names to remove", key="extra_names",
                                    help="People mentioned in the materials who are not speakers. Speaker names are "
                                         "removed automatically; these are replaced with [team member] too.")
    st.subheader("2 · Process materials")
    src_tab_drive, src_tab_upload = st.tabs(["Google Drive", "Upload"])
    with src_tab_drive:
        if drive.available():
            folder_id = st.text_input("Drive folder ID", value=os.environ.get("DRIVE_FOLDER_ID", ""),
                                      help="The part of the folder URL after /folders/. Share the folder with the service-account email first.")
            if st.button("Browse folder", use_container_width=True, disabled=not folder_id):
                try:
                    ss.drive_files = drive.list_folder(folder_id)
                    if not ss.drive_files:
                        st.warning("No supported files found. Is the folder shared with the service account?")
                except Exception as e:  # noqa: BLE001
                    st.error(f"Drive error: {e}")
            if ss.get("drive_files"):
                chosen = st.multiselect("Select materials", options=[f["name"] for f in ss.drive_files],
                                        default=[f["name"] for f in ss.drive_files])
                if st.button("Import selected from Drive", use_container_width=True, disabled=not chosen):
                    picked = [f for f in ss.drive_files if f["name"] in chosen]
                    with st.spinner("Pulling files from Drive…"):
                        ss.files = [drive.fetch(f) for f in picked]
                    ss.model, ss.parsed = None, []
                    _reset_review_state()
                    st.success(f"Imported {len(ss.files)} file(s)")
        else:
            st.caption("Drive import not configured — add `service_account.json` next to app.py "
                       "(see ingest/drive.py for the 5-step setup). Use Upload or demo materials meanwhile.")
    with src_tab_upload:
        uploads = st.file_uploader("Upload documents & transcripts",
                                   type=["md", "txt", "vtt", "srt", "docx", "pdf"],
                                   accept_multiple_files=True)
    c1, c2 = st.columns(2)
    if c1.button("Load demo materials", use_container_width=True):
        ss.files = [(p.name, p.read_bytes()) for p in sorted(DEMO_DIR.glob("*.md"))]
        ss.model, ss.parsed, ss.mode = None, [], None
        _reset_review_state()
    if c2.button("Clear", use_container_width=True):
        ss.files, ss.parsed, ss.model, ss.mode = [], [], None, None
        _reset_review_state()
    # only take the uploader's files when its contents change, so an opened project isn't overwritten
    uploads_sig = tuple((u.name, u.size) for u in uploads or [])
    if uploads and uploads_sig != ss.get("_uploads_sig"):
        ss.files = [(u.name, u.getvalue()) for u in uploads]
    ss._uploads_sig = uploads_sig
    if ss.files:
        st.caption("Selected:")
        for n, b in ss.files:
            st.markdown(f"- `{n}` ({len(b):,} bytes)")
    st.divider()
    key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
    st.caption(("🟢 Live extraction available" if key_present else "🟡 No API key — cached extraction only"))
    force_live = st.checkbox("Run agent live (ignore cache / replay)", value=False, disabled=not key_present)
    run = st.button("▶ Analyze process", type="primary", use_container_width=True, disabled=not ss.files)

# ---------------------------------------------------------------- pipeline
STEP_ICON = {"code": "⚙️", "llm": "🤖"}

if run:
    ss.model, ss.run = None, None
    ss.map_reviewed, ss.framework = False, None
    ss["map_reviewed_toggle"] = False
    with st.status("Running Stage 1 agent…", expanded=True) as status:
        st.write("⚙️ **Parse** — splitting files into chunks with source references")
        ss.parsed = [parse_file(n, b) for n, b in ss.files]
        for pf in ss.parsed:
            st.write(f"  · {pf.summary}")

        def on_step(step):
            icon = "⚠️" if step.status != "ok" else STEP_ICON[step.kind]
            st.write(f"{icon} **{step.name}** — {step.summary}")

        try:
            agent_run = run_agent(ss.parsed, ss.files, hint=f"{process_name}. {process_scope}",
                                  force_live=force_live, progress=on_step)
        except Exception as e:  # noqa: BLE001
            status.update(label="Agent failed", state="error")
            st.error(str(e))
            st.stop()
        model = dedupe_activities(agent_run.final)
        model.process.name = process_name or model.process.name
        model.process.owner = process_owner or model.process.owner
        ss.model, ss.mode, ss.run = model, agent_run.mode, agent_run
        status.update(label="Stage 1 complete", state="complete", expanded=False)

model: ProcessModel | None = ss.model
if model is None:
    st.info("Upload process materials (or **Load demo materials**) in the sidebar, then click **Analyze process**.")
    st.stop()

cov = source_coverage(model)
st.markdown(f"<span class='mode'>agent: {ss.mode}</span>&nbsp; "
            f"<span class='mode'>{len(model.roles)} roles</span>&nbsp;"
            f"<span class='mode'>{len([a for a in model.activities if a.kind not in ('start','end')])} activities</span>&nbsp;"
            f"<span class='mode'>{len(model.decisions)} decisions</span>&nbsp;"
            f"<span class='mode'>{len(model.pain_points)} pain points</span>&nbsp;"
            f"<span class='mode'>{sum(1 for v in cov.values() if v>=2)} steps corroborated by 2+ sources</span>",
            unsafe_allow_html=True)

# names to keep out of every output (speaker labels + names typed in the intake), as the handoff does
names = speaker_names(ss.files) + with_nicknames([n.strip() for n in extra_names.split(",") if n.strip()])

VIEW_STAGE1, VIEW_PHASE3 = "Stage 1 · Current state", "Phase 3 · Future state"
ss.setdefault("view", VIEW_STAGE1)
view = st.segmented_control("View", [VIEW_STAGE1, VIEW_PHASE3], key="view", label_visibility="collapsed")
if view == VIEW_PHASE3:
    render_phase3(model, names)
    st.stop()

tab_swim, tab_model, tab_pain, tab_json, tab_src, tab_agent = st.tabs(
    ["🏊 Swimlane", "🧩 Process model", "⚠️ Pain points & open items", "{ } Data contract", "📄 Sources", "🤖 Agent run"])

# ---------------------------------------------------------------- agent run
KIND_BADGE = {"merged": "🔗 merged", "conflict": "⚖️ conflict", "single-source": "1️⃣ single-source",
              "renamed": "🏷️ renamed", "excluded": "🚫 excluded"}
SEV_BADGE = {"high": "🔴 high", "medium": "🟠 medium", "low": "⚪ low"}
SEV_BG = {"high": "#FDECEC", "medium": "#FFF4E5", "low": "#F3F4F6"}
DTYPE = {"missing_in_source": "missing in a source", "order": "order", "role": "owner / lane",
         "logic": "branching / loop", "terminology": "naming", "detail": "detail"}


def short(name: str) -> str:
    return name.replace("sales_process_", "").replace(".md", "").replace("_", " ")


def json_panel(ar, key: str, label: str = "View output JSON"):
    data = ar.artifact(key) if key else None
    if data is None:
        return
    with st.expander(f"{{ }} {label}"):
        st.download_button("Download JSON", json.dumps(data, indent=2), file_name=f"{key.replace(':', '_')}.json",
                           mime="application/json", key=f"dl_{key}")
        st.json(data, expanded=1)


def mini_svg(model, title):
    return (f"<div style='overflow:auto;background:#fff'><div style='transform:scale(.55);transform-origin:0 0;"
            f"width:max-content'>{render_svg(model, source_coverage(model), title=title, show_stickies=False)}</div></div>")


with tab_agent:
    ar = ss.run
    if ar is None:
        st.info("No Stage 1 agent run in this session (the project was saved without one). "
                "Click **Analyze process** to run the agent.")
    else:
        if ar.mode == "replay":
            st.info("**Replay** of a pre-computed agent run for the demo transcripts (no live API call). "
                    "With an API key, tick *Run agent live* to execute these steps on any materials.")
        top1, top2 = st.columns([3, 1])
        top1.markdown("##### Agent trace")
        top2.download_button("⬇ Download full run (JSON)", ar.model_dump_json(indent=2), file_name="agent_run.json",
                             mime="application/json", use_container_width=True)
        st.caption("Extractor, comparer, reconciler and reviser share one model; the **verifier runs on a different model**, "
                   "builds its checklist from the transcripts before seeing any map, and never edits the map itself.")
        for stp in ar.steps:
            icon = "⚠️" if stp.status != "ok" else STEP_ICON[stp.kind]
            meta = " · ".join(x for x in [stp.agent, stp.model or "code", f"{stp.duration_s}s" if stp.duration_s else ""] if x)
            st.markdown(f"{icon} **{stp.id} · {stp.name}** <span class='mode'>{meta}</span><br>"
                        f"<span style='color:#4B5563'>{stp.summary}</span>", unsafe_allow_html=True)
            if stp.artifact:
                json_panel(ar, stp.artifact)

        # ---- 1. per-source
        if ar.per_source:
            st.divider()
            st.markdown("##### 1 · Independent extraction per source")
            st.caption("Each source is mapped on its own first, so the differences show up instead of being blended.")
            cols = st.columns(len(ar.per_source))
            for col, (name, pm) in zip(cols, ar.per_source.items()):
                with col:
                    acts = len([a for a in pm.activities if a.kind not in ("start", "end")])
                    st.markdown(f"**{short(name)}**")
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Activities", acts); m2.metric("Decisions", len(pm.decisions)); m3.metric("Pain points", len(pm.pain_points))
            with st.expander("Compare per-source swimlanes"):
                for name, pm in ar.per_source.items():
                    st.markdown(f"**{short(name)}**")
                    components.html(mini_svg(pm, f"From {short(name)} only"), height=330, scrolling=True)

        # ---- 2. discrepancies
        if ar.alignment or ar.discrepancies:
            st.divider()
            st.markdown("##### 2 · Discrepancies between sources")
            if ar.alignment:
                files = list(ar.per_source.keys())
                rows = []
                for a in ar.alignment:
                    row = {"step": a.step, "type": a.kind}
                    for f in files:
                        row[short(f)] = "✓" if a.per_source.get(f) else "—"
                    rows.append(row)
                cov_df = pd.DataFrame(rows)
                gaps = cov_df[[short(f) for f in files]].eq("—").any(axis=1)
                st.caption(f"Coverage matrix: {len(cov_df)} steps lined up across sources; "
                           f"**{int(gaps.sum())} appear in only some sources** (highlighted).")
                styled = cov_df.style.apply(lambda r: ["background-color:#FFF4E5" if gaps[r.name] else "" for _ in r], axis=1)
                st.dataframe(styled, hide_index=True, use_container_width=True, height=min(420, 38 + 35 * len(cov_df)))
            if ar.discrepancies:
                sevs = {s: sum(d.severity == s for d in ar.discrepancies) for s in ("high", "medium", "low")}
                st.caption(" · ".join(f"{SEV_BADGE[k]} {v}" for k, v in sevs.items() if v))
                resolved_by = {}
                for r in ar.reconciliation:
                    for i in r.discrepancy_ids:
                        resolved_by[i] = r
                for d in sorted(ar.discrepancies, key=lambda x: ("high", "medium", "low").index(x.severity)):
                    st.markdown(f"<div style='background:{SEV_BG[d.severity]};padding:10px 14px;border-radius:8px;margin:6px 0'>"
                                f"<b>{d.id} · {d.subject}</b> &nbsp;<span class='mode'>{SEV_BADGE[d.severity]}</span>"
                                f" <span class='mode'>{DTYPE.get(d.type, d.type)}</span></div>", unsafe_allow_html=True)
                    cols = st.columns(max(1, len(d.per_source)))
                    for col, (f, said) in zip(cols, d.per_source.items()):
                        col.markdown(f"**{short(f)}**  \n{said}")
                    r = resolved_by.get(d.id)
                    if r:
                        st.markdown(f"↳ **Resolved ({KIND_BADGE[r.kind]}):** {r.resolution}")
                    else:
                        st.markdown("↳ ⚠️ *Not addressed by the reconciler*")
                    for sr in d.sources:
                        st.caption(f"{sr.file} @ {sr.location} — {sr.quote}")

        # ---- 3. reconciliation
        if ar.reconciliation:
            st.divider()
            st.markdown("##### 3 · Reconciliation — how the sources were combined")
            kinds = {}
            for r in ar.reconciliation:
                kinds[r.kind] = kinds.get(r.kind, 0) + 1
            st.caption(" · ".join(f"{KIND_BADGE[k]} {v}" for k, v in kinds.items()))
            order = {"conflict": 0, "single-source": 1, "renamed": 2, "excluded": 3, "merged": 4}
            for r in sorted(ar.reconciliation, key=lambda x: order[x.kind]):
                tag = f" (resolves {', '.join(r.discrepancy_ids)})" if r.discrepancy_ids else ""
                with st.expander(f"{KIND_BADGE[r.kind]} — {r.subject}{tag}"):
                    st.markdown(f"**What the sources said:** {r.detail}")
                    st.markdown(f"**Resolution:** {r.resolution}")
                    for sr in r.sources:
                        st.markdown(f"↳ `{sr.file}` @ {sr.location} — *{sr.quote}*")

        # ---- 4. independent verification
        if ar.checklist or ar.findings:
            st.divider()
            st.markdown("##### 4 · Independent verification")
            st.caption("The verifier built this checklist from the transcripts **before** seeing the map, checked the map "
                       "against it, and reported findings. A separate reviser applied or rejected each one.")
            if ar.checklist:
                flagged = {c for f in ar.findings for c in f.checklist_ids}
                with st.expander(f"Verifier's blind checklist ({len(ar.checklist)} items)"):
                    st.dataframe(pd.DataFrame([{"id": c.id, "kind": c.kind, "item": c.description, "role": c.role,
                                                "flagged": "⚑" if c.id in flagged else ""} for c in ar.checklist]),
                                 hide_index=True, use_container_width=True)
            for rnd in sorted({f.round for f in ar.findings}):
                st.markdown(f"**Round {rnd}**")
                for f in [x for x in ar.findings if x.round == rnd]:
                    with st.container(border=True):
                        st.markdown(f"{SEV_BADGE[f.severity]} · `{f.issue}` · **{f.id}** — {f.description}")
                        c1, c2 = st.columns(2)
                        c1.markdown(f"**Verifier suggests:** {f.fix}")
                        c2.markdown(f"**Reviser:** {'✅ applied' if f.applied else '❌ rejected'} — {f.reviser_note}")
                        refs = (f"checklist {', '.join(f.checklist_ids)} · " if f.checklist_ids else "")
                        for sr in f.evidence:
                            st.caption(f"{refs}{sr.file} @ {sr.location} — {sr.quote}")
            if ar.draft is not None:
                with st.expander("Before vs after verification"):
                    components.html(mini_svg(ar.draft, "Draft — before verification"), height=330, scrolling=True)
                    st.markdown("**Final:** see the Swimlane tab.")

# ---------------------------------------------------------------- sources
with tab_src:
    st.markdown(f"**{model.process.name}** — owner: {model.process.owner}")
    st.write(model.process.summary)
    for pf in ss.parsed:
        with st.expander(pf.summary):
            for c in pf.chunks[:40]:
                st.markdown(f"**[{c.location}]** {c.text[:600]}{'…' if len(c.text) > 600 else ''}")

# ---------------------------------------------------------------- model (editable)
with tab_model:
    st.caption("Human review: edit names, lanes, step types or order, then **Apply edits** to regenerate the swimlane.")
    role_names = {r.id: r.name for r in model.roles}
    name_to_role = {v: k for k, v in role_names.items()}

    st.markdown("##### Roles (swimlanes)")
    roles_df = pd.DataFrame([{"id": r.id, "name": r.name, "description": r.description, "external": r.is_external} for r in model.roles])
    roles_edit = st.data_editor(roles_df, hide_index=True, use_container_width=True, key="roles_editor",
                                column_config={"id": st.column_config.TextColumn(disabled=True)})

    st.markdown("##### Activities & decisions")
    rows = []
    for a in model.activities:
        rows.append({"id": a.id, "type": a.kind, "name": a.name, "role": role_names.get(a.role_id, a.role_id),
                     "order": a.order, "step_type": a.step_type, "tools": ", ".join(a.tools),
                     "CGS tags": ", ".join(t.text if t.on_list else f"{t.text} (not on CGS list)" for t in tags_for(a.tools)),
                     "sources": cov.get(a.id, 0), "description": a.description})
    for d in model.decisions:
        rows.append({"id": d.id, "type": "decision", "name": d.question, "role": role_names.get(d.role_id, d.role_id),
                     "order": d.order, "step_type": "", "tools": "", "CGS tags": "",
                     "sources": cov.get(d.id, 0), "description": " / ".join(f"{b.label}→{b.next_id}" for b in d.branches)})
    df = pd.DataFrame(rows).sort_values(["order", "id"])
    edited = st.data_editor(
        df, hide_index=True, use_container_width=True, height=520, key="nodes_editor",
        column_config={
            "id": st.column_config.TextColumn(disabled=True, width="small"),
            "type": st.column_config.TextColumn(disabled=True, width="small"),
            "role": st.column_config.SelectboxColumn(options=list(role_names.values()), width="medium"),
            "step_type": st.column_config.SelectboxColumn(options=["deterministic", "judgment", "unclear", ""], width="small"),
            "order": st.column_config.NumberColumn(width="small", step=1),
            "sources": st.column_config.NumberColumn(disabled=True, width="small"),
            "tools": st.column_config.TextColumn(help="Comma-separated tool names, e.g. Insightly, Calendar. "
                                                      "Mapped to CGS tool IDs on Apply."),
            "CGS tags": st.column_config.TextColumn(disabled=True, help="Resolved from the CGS tool list"),
        })

    st.markdown("##### Step sizing (for CGS opportunity scoring)")
    st.caption("purpose: intent = sets the goal, execution = does the work, audit = checks or approves · "
               "control_now: C0 manual … C5 strategic operator · minutes: human time each time the process runs. "
               "Each value is marked **stated** (from the transcripts) or **estimated**.")
    srows = []
    for n in sorted(list(model.activities) + list(model.decisions), key=lambda x: (x.order, x.id)):
        if getattr(n, "kind", "decision") in ("start", "end"):
            continue
        rng = n.human_minutes_range or [None, None]
        srows.append({"id": n.id, "step": getattr(n, "name", None) or getattr(n, "question", ""),
                      "purpose": n.purpose or "", "purpose basis": n.sizing_basis.purpose,
                      "control_now": n.control_now or "", "control basis": n.sizing_basis.control_now,
                      "minutes": n.human_minutes_per_cycle, "min (low)": rng[0], "min (high)": rng[1],
                      "minutes basis": n.sizing_basis.human_minutes_per_cycle,
                      "judgment reason stated": {True: "yes", False: "no"}.get(n.judgment_reason_stated, "")})
    basis_opts = ["stated", "estimated"]
    sized = st.data_editor(
        pd.DataFrame(srows), hide_index=True, use_container_width=True, key="sizing_editor",
        column_config={
            "id": st.column_config.TextColumn(disabled=True, width="small"),
            "step": st.column_config.TextColumn(disabled=True),
            "purpose": st.column_config.SelectboxColumn(options=["intent", "execution", "audit", ""], width="small"),
            "purpose basis": st.column_config.SelectboxColumn(options=basis_opts, width="small"),
            "control_now": st.column_config.SelectboxColumn(options=["C0", "C1", "C2", "C3", "C4", "C5", ""], width="small"),
            "control basis": st.column_config.SelectboxColumn(options=basis_opts, width="small"),
            "minutes": st.column_config.NumberColumn(min_value=0, step=1, width="small"),
            "min (low)": st.column_config.NumberColumn(min_value=0, step=1, width="small"),
            "min (high)": st.column_config.NumberColumn(min_value=0, step=1, width="small"),
            "minutes basis": st.column_config.SelectboxColumn(options=basis_opts, width="small"),
            "judgment reason stated": st.column_config.SelectboxColumn(options=["yes", "no", ""], width="small"),
        })

    if st.button("Apply edits & regenerate", type="primary"):
        for _, r in roles_edit.iterrows():
            for role in model.roles:
                if role.id == r["id"]:
                    role.name, role.description, role.is_external = r["name"], r["description"] or "", bool(r["external"])
        name_to_role = {r.name: r.id for r in model.roles}
        for _, r in edited.iterrows():
            rid = name_to_role.get(r["role"])
            for a in model.activities:
                if a.id == r["id"]:
                    a.name = r["name"]; a.order = int(r["order"])
                    if rid: a.role_id = rid
                    if r["step_type"] in ("deterministic", "judgment", "unclear"): a.step_type = r["step_type"]
                    a.tools = [t.strip() for t in str(r["tools"] or "").split(",") if t.strip()]
                    a.description = r["description"] or ""
            for d in model.decisions:
                if d.id == r["id"]:
                    d.question = r["name"]; d.order = int(r["order"])
                    if rid: d.role_id = rid
        num = lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else float(v)  # noqa: E731
        for _, r in sized.iterrows():
            n = next((x for x in list(model.activities) + list(model.decisions) if x.id == r["id"]), None)
            if n is None:
                continue
            n.purpose = r["purpose"] or None
            n.control_now = r["control_now"] or None
            n.human_minutes_per_cycle = num(r["minutes"])
            lo, hi = num(r["min (low)"]), num(r["min (high)"])
            n.human_minutes_range = [lo, hi] if lo is not None and hi is not None else None
            n.sizing_basis.purpose = r["purpose basis"] or "estimated"
            n.sizing_basis.control_now = r["control basis"] or "estimated"
            n.sizing_basis.human_minutes_per_cycle = r["minutes basis"] or "estimated"
            n.judgment_reason_stated = {"yes": True, "no": False}.get(r["judgment reason stated"])
        ss.model = model
        ss.map_reviewed, ss._reset_reviewed = False, True
        st.success("Model updated — see the Swimlane tab.")
        st.rerun()

    with st.expander("Handoffs & dependencies"):
        st.dataframe(pd.DataFrame([{"from": role_names.get(h.from_role_id), "to": role_names.get(h.to_role_id),
                                    "at": h.at_node_id, "description": h.description} for h in model.handoffs]),
                     hide_index=True, use_container_width=True)
        st.dataframe(pd.DataFrame([{"node": d.node_id, "depends on": d.depends_on, "type": d.type} for d in model.dependencies]),
                     hide_index=True, use_container_width=True)

    with st.expander("Evidence — source references per step"):
        for a in model.activities:
            if a.source_refs:
                st.markdown(f"**{a.id} · {a.name}**")
                for r in a.source_refs:
                    st.markdown(f"&nbsp;&nbsp;↳ `{r.file}` @ {r.location} — *{r.quote}*")
        for d in model.decisions:
            if d.source_refs:
                st.markdown(f"**{d.id} · {d.question}**")
                for r in d.source_refs:
                    st.markdown(f"&nbsp;&nbsp;↳ `{r.file}` @ {r.location} — *{r.quote}*")

# ---------------------------------------------------------------- swimlane
with tab_swim:
    c0, c1, c1b, c2 = st.columns([2, 2, 2, 3])
    overlay = c0.toggle("Analysis overlay (step type, pain points, source counts)", value=False)
    stickies = c1.toggle("Show open-item sticky notes", value=True)
    tools_on = c1b.toggle("Show CGS tool tags", value=True)
    zoom = c2.slider("Zoom", 0.4, 1.6, 0.8, 0.05)
    geo = build_geometry(model, show_stickies=stickies, show_tools=tools_on)
    svg = render_svg(model, cov, overlay=overlay, geometry=geo)
    components.html(
        f"""<div style="overflow:auto;border:1px solid #E5E7EB;border-radius:8px;background:#fff;padding:8px">
              <div style="transform:scale({zoom});transform-origin:0 0;width:max-content">{svg}</div></div>""",
        height=640, scrolling=True)
    drawio_xml = render_drawio(model, geo)
    d1, d2, d4, d3 = st.columns(4)
    d1.download_button("⬇ Download SVG", svg, file_name="current_state_swimlane.svg", mime="image/svg+xml", use_container_width=True)
    try:
        d2.download_button("⬇ Download PNG", render_png(svg), file_name="current_state_swimlane.png", mime="image/png", use_container_width=True)
    except Exception:  # cairosvg missing
        d2.caption("PNG export needs `cairosvg`")
    d4.download_button("⬇ Download draw.io (editable)", drawio_xml, file_name="current_state_swimlane.drawio",
                       mime="application/xml", use_container_width=True,
                       help="Opens in draw.io, or in Lucidchart via File → Import → draw.io. Shapes, lanes and "
                            "connectors stay editable.")
    d3.download_button("⬇ Download model JSON", model.model_dump_json(indent=2), file_name="process_model.json",
                       mime="application/json", use_container_width=True)
    s1, s2 = st.columns([1, 2])
    ss.map_reviewed = s2.toggle("Mark map as reviewed", key="map_reviewed_toggle",
                                help="Records that someone has checked this map against the source material.")
    intake_now = {k: ss.get(k, "") for k in INTAKE_KEYS}
    fw = ss.framework
    s1.download_button("💾 Save project", save_project(
        ss.files, intake_now, model, ss.run, ss.mode, ss.map_reviewed,
        fw.model_dump() if hasattr(fw, "model_dump") else fw),
        file_name="cgs_project.json", mime="application/json", use_container_width=True,
        help="Saves the materials, the map, the agent run and your answers so you can pick up later.")
    # ---- edit in draw.io, then bring the edits back
    st.markdown("##### Edit in draw.io")
    e1, e2 = st.columns([1, 2])
    e1.link_button("✏️ Open in draw.io", drawio_url(drawio_xml), use_container_width=True,
                   help="Opens this diagram in draw.io in your browser. No account needed; the diagram travels in the "
                        "link itself and is not uploaded anywhere by the app.")
    e2.caption("1. **Open in draw.io** and edit: drag steps (or into another lane), rename, add or remove steps and "
               "connectors, delete or edit tool tags.  \n"
               "2. Save it: **File → Save as → Device** (or Google Drive) as a `.drawio` file.  \n"
               "3. Upload it below to bring the changes back into the process model. The app redraws the layout.")
    up = st.file_uploader("Upload edited draw.io file", type=["drawio", "xml"], key="drawio_upload")
    if up is not None:
        try:
            res = import_drawio(up.getvalue().decode("utf-8"), model)
        except Exception as ex:  # noqa: BLE001
            st.error(f"Couldn't read that file: {ex}")
        else:
            with st.container(border=True):
                st.markdown(f"**Changes found ({len(res.changes)})**")
                for c in res.changes:
                    st.markdown(f"- {c}")
                for w in res.warnings:
                    st.warning(w)
                if st.button("Apply changes to the model", type="primary",
                             disabled=res.changes == ["No changes found — the file matches the current model."]):
                    ss.model = res.model
                    ss.map_reviewed, ss._reset_reviewed = False, True
                    st.success("Model updated from draw.io.")
                    st.rerun()

    st.caption("Drawn to the CGS Process Documentation Standards: horizontal swimlanes by role, BPMN shapes "
               "(events, tasks, decisions as questions with Yes/No, ⊞ predefined subprocess), right-angle connectors, "
               "lettered in-page connectors for long backward jumps, CGS tool tags above each task, black & white core with colour reserved for callouts. "
               "Yellow notes flag open decisions / missing rules; italic text under a task is reviewer context.")

# ---------------------------------------------------------------- pain points
with tab_pain:
    node_name = {a.id: a.name for a in model.activities} | {d.id: d.question for d in model.decisions}
    for p in model.pain_points:
        with st.container(border=True):
            st.markdown(f"**{p.id} · {p.description}**")
            st.caption(f"category: {p.category} · at: {node_name.get(p.node_id, p.node_id or '—')}")
            for r in p.source_refs:
                st.markdown(f"↳ `{r.file}` @ {r.location} — *{r.quote}*")
    st.markdown("##### Frequency (for the handoff)")
    fdf = pd.DataFrame([{"id": p.id, "pain point": p.description[:90], "frequency": p.frequency or "",
                         "basis": p.frequency_basis} for p in model.pain_points])
    fed = st.data_editor(fdf, hide_index=True, use_container_width=True, key="freq_editor",
                         column_config={"id": st.column_config.TextColumn(disabled=True, width="small"),
                                        "pain point": st.column_config.TextColumn(disabled=True),
                                        "frequency": st.column_config.SelectboxColumn(
                                            options=["per_day", "per_week", "per_month", "ad_hoc", ""], width="small"),
                                        "basis": st.column_config.SelectboxColumn(options=["stated", "estimated"], width="small")})
    if st.button("Save frequencies"):
        for _, r in fed.iterrows():
            for p in model.pain_points:
                if p.id == r["id"]:
                    p.frequency = r["frequency"] or None
                    p.frequency_basis = r["basis"] or "estimated"
        ss.model = model
        ss.map_reviewed, ss._reset_reviewed = False, True
        st.success("Saved.")
    if model.open_questions:
        st.markdown("##### Open questions for the process owner")
        for q in model.open_questions:
            st.markdown(f"- {q}")

# ---------------------------------------------------------------- json
with tab_json:
    j1, j2 = st.tabs(["Process model", "Pain-point handoff (opportunity mapping)"])
    with j1:
        st.caption("This is the Stage 1 → Stage 2 data contract. Analysis (Stage 2) consumes exactly this structure.")
        st.json(json.loads(model.model_dump_json()), expanded=False)
    with j2:
        used = sorted({s.model for s in (ss.run.steps if ss.run else []) if s.model})
        h = build_handoff(model, account_id=account_id, process_id=process_id, function_ref=function_ref,
                          definition_status=definition_status, names=names, mode=ss.mode, models_used=used)
        problems = check_handoff(h, names)
        st.caption(f"Format `{h['schema']}` — CGS's proposed v0.1 with small additions. "
                   f"{len(h['activities'])} activities, {len(h['pain_points'])} pain points. "
                   "Names are replaced with roles / [team member]; `grounded` is left empty (no automated check).")
        if problems:
            st.warning("Check: " + "; ".join(problems))
        else:
            st.success("Check passed: allowed values, references and quote lengths are valid; no names found.")
        st.download_button("⬇ Download pain-point handoff JSON", json.dumps(h, indent=2, ensure_ascii=False),
                           file_name=f"painpoints_{(process_id or 'process').lower()}.json", mime="application/json")
        st.json(h, expanded=False)
