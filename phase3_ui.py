"""Phase 3 · Future state screens (Run, Gate & context, Steps, Follow-ups, Future state).

Called from app.py with the reviewed Stage 1 map. Each screen is wrapped in `safe()` so an
error in one shows `st.error` there and the other screens still render. Answers a person
gives go through `answer_and_rescore` (code only, no AI call).
"""
from __future__ import annotations

import json
import os

import pandas as pd
import streamlit as st

from framework.agent import framework_hash, run_framework
from framework.future import build_future_model
from framework.models import Answer, FrameworkRun, allowed_values
from framework.followups import ensure_top_questions
from framework.outputs import (follow_ups_markdown, skipped_at_low_gate, step_table, step_table_csv,
                               summary_markdown, top_questions_heading)
from framework.rules import _step_answers, _tier_label, answer_and_rescore, apply_rules, undo_answer
from framework.settings import load_settings
from handoff import _scrub, with_nicknames
from render.drawio import drawio_url, render_drawio
from render.swimlane import build_geometry, render_png, render_svg
from schema import ProcessModel

STEP_ICON = {"code": "⚙️", "llm": "🤖"}
BASIS_BADGE = {"stated": "🟢 stated", "assumed": "🟡 assumed", "answered": "🔵 answered"}
CONF = {"H": "high", "M": "medium", "L": "low"}
FAMILY_LABEL = {
    "worksheet": "Worksheet (what the step is today)",
    "r_pass1": "Should it exist? (R1-R7, pass 1)",
    "r_pass2": "Should it exist? (R1-R7, pass 2 with context)",
    "r_move": "Move or remove (R4)",
    "accountability": "Accountability",
    "combine_with": "Combine with",
    "tier1": "Tier 1 checks (D1-D3)",
    "dials": "Dials (1-5)",
    "agent_test": "Agent test (A1-A4)",
    "readiness": "Readiness for this step (overrides)",
}
GATE_LABEL = {"1": "Start and ends marked", "2": "Lanes are roles", "3": "Tasks are verb + noun",
              "4": "Decisions are questions", "5": "Main path and loops", "6": "Systems named",
              "7": "Subprocesses linked and owned", "8": "SOP for human steps"}


def safe(fn, *args):
    """Run one screen; an exception becomes an error box on that screen only."""
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001 - st.stop / st.rerun are BaseException and pass through
        st.error(f"This screen hit an error ({type(e).__name__}: {e}). The other screens still work.")
        with st.expander("Error details"):
            st.exception(e)


class _Ctx:
    def __init__(self, model: ProcessModel, names: list[str], save=None):
        self.save = save          # () -> project JSON, for Save project on the Future state screen
        self.model, self.names = model, with_nicknames(list(names)) if names else []
        self.s = load_settings()

    def clean(self, text) -> str:
        text = "" if text is None else str(text)
        return _scrub(text, self.names) if self.names and text else text

    def label(self, sid: str) -> str:
        for a in self.model.activities:
            if a.id == sid:
                return a.name
        for d in self.model.decisions:
            if d.id == sid:
                return d.question
        return sid


def _ss():
    return st.session_state


def _set_run(run: FrameworkRun | None):
    """Store a new run; bump the version so answer widgets start from the new values."""
    ss = _ss()
    ss.framework = run
    ss.p3_ver = ss.get("p3_ver", 0) + 1


def _answer(c: _Ctx, run: FrameworkRun, path: str, value) -> FrameworkRun:
    """Record one answer and rescore (code only); names keep the roles-not-people gate check right."""
    return answer_and_rescore(run, c.model, path, value, c.s, names=c.names)


def _quote(c: _Ctx, a: Answer) -> str:
    return " | ".join(c.clean(f"{q.file} @ {q.location}: {q.quote}") for q in a.quotes)


def _answer_rows(c: _Ctx, items) -> pd.DataFrame:
    return pd.DataFrame([{"answer": k, "value": c.clean(a.value),
                          "basis": BASIS_BADGE.get(a.basis, a.basis), "confidence": CONF.get(a.confidence, a.confidence),
                          "reason": c.clean(a.reason), "quote": _quote(c, a), "question": c.clean(a.question)}
                         for k, a in items])


def _input(path: str, current, key: str, blank: bool = False, label: str | None = None):
    """A selectbox for answers with allowed values, else a text box."""
    label = label or path.split(".")[-1]
    run = _ss().get("framework")
    allowed = allowed_values(path, list(run.steps) if run is not None else None)   # combine_with: step ids
    if allowed is not None:
        opts = list(allowed)
        idx = None if blank else next((i for i, o in enumerate(opts) if str(o) == str(current)), None)
        return st.selectbox(label, opts, index=idx, key=key, placeholder="Choose…")
    return st.text_input(label, value="" if blank or current is None else str(current), key=key)


def _changed(new, current) -> bool:
    if new is None:
        return False
    if isinstance(new, str) and not new.strip():
        return False
    return str(new).strip() != ("" if current is None else str(current))


def _apply(c: _Ctx, changes: list[tuple[str, object]], msg_key: str):
    """Apply answers one by one; keep the good ones, report the bad ones."""
    ss = _ss()
    run, errors, done = ss.framework, [], 0
    for path, value in changes:
        try:
            run = _answer(c, run, path, value)
            done += 1
        except (ValueError, KeyError) as e:
            errors.append(f"{path}: {e}")
    if done:
        _set_run(run)
    ss[msg_key] = (done, errors)
    st.rerun()


def _show_msg(msg_key: str):
    done, errors = _ss().pop(msg_key, (0, []))
    if done:
        st.success(f"Saved {done} answer(s) and rescored (no AI call).")
    for e in errors:
        st.error(e)


# ------------------------------------------------------------------------- 1. Run
def _run_screen(c: _Ctx):
    ss = _ss()
    key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not ss.get("map_reviewed"):
        st.warning("The map isn't marked as reviewed. Check it against the materials and switch on **Mark map as "
                   "reviewed** on the Stage 1 Swimlane tab first; a run on an unreviewed map is recorded as such.")
    b1, b2, _ = st.columns([1, 1, 2])
    go = b1.button("▶ Run framework", type="primary", width="stretch", key="p3_run")
    again = b2.button("↻ Re-run AI steps", width="stretch", key="p3_rerun", disabled=not key_present,
                      help="Ignore the saved run and call the AI again" if key_present else "Needs ANTHROPIC_API_KEY")
    if go or again:
        prev = ss.framework
        with st.status("Running the Process Improvement Framework…", expanded=True) as status:
            def on_step(stp):
                icon = "⚠️" if stp.status != "ok" else STEP_ICON.get(stp.kind, "•")
                st.write(f"{icon} **{stp.name}** — {c.clean(stp.summary)}")
            try:
                run = run_framework(c.model, ss.parsed, agent_run=ss.run, map_reviewed=bool(ss.get("map_reviewed")),
                                    success_end=prev.success_end if prev else None, force_live=again,
                                    progress=on_step, s=c.s, names=c.names, prior=prev)
            except Exception as e:  # noqa: BLE001
                failure = str(e)
                status.update(label="Framework run failed", state="error", expanded=False)
            else:
                failure = None
                _set_run(run)
                status.update(label="Framework run complete", state="complete", expanded=False)
        if failure:
            st.error(failure)

    run: FrameworkRun | None = ss.framework
    if run is None:
        st.info("No framework run yet. Click **Run framework**. Without an API key, only maps with a saved run "
                "(the demo replay, or one cached on this machine) can be run.")
        return
    if run.mode == "replay":
        st.info("**Replay** of a pre-computed framework run for the demo map (no live API call).")
    _low_gate_note(run)
    m = st.columns(5)
    m[0].metric("Mode", run.mode)
    m[1].metric("Documentation", run.gate_band, f"score {run.gate_score:g}", delta_color="off")
    m[2].metric("Redesign potential", f"{run.redesign_potential} of 5")
    m[3].metric("Follow-ups", len(run.follow_ups))
    m[4].metric("Map reviewed", "yes" if run.map_reviewed else "no")
    if run.posture:
        st.markdown("**Posture:** " + c.clean("; ".join(run.posture)))
    with st.expander(f"Framework trace ({len(run.trace)} steps)", expanded=False):
        st.caption("AI steps read the materials and answer questions; code makes every call, applies every threshold "
                   "and computes every score. The verifier runs on a different model and can only downgrade.")
        for stp in run.trace:
            icon = "⚠️" if stp.status != "ok" else STEP_ICON.get(stp.kind, "•")
            meta = " · ".join(x for x in [stp.agent, stp.model or "code",
                                          f"{stp.duration_s}s" if stp.duration_s else ""] if x)
            st.markdown(f"{icon} **{stp.id} · {stp.name}** <span class='mode'>{meta}</span><br>"
                        f"<span style='color:#4B5563'>{c.clean(stp.summary)}</span>", unsafe_allow_html=True)
            if stp.artifact and stp.artifact in run.artifacts:
                text = c.clean(json.dumps(run.artifacts[stp.artifact], ensure_ascii=False, default=str))
                try:
                    st.json(json.loads(text), expanded=False)
                except ValueError:
                    st.code(text, language="json")
    st.download_button("⬇ Download full run (JSON)", c.clean(run.model_dump_json(indent=2)),
                       file_name="framework_run.json", mime="application/json", key="p3_dl_run_top")


def _low_gate_note(run: FrameworkRun):
    if skipped_at_low_gate(run):
        st.info("AI steps were skipped at the Low gate — re-run AI steps to score steps from the materials.")


def _need_run(ss) -> FrameworkRun | None:
    run = ss.get("framework")
    if run is None:
        st.info("Run the framework first (Run tab).")
    return run


# ------------------------------------------------------------- 2. Gate & context
def _override_box(c: _Ctx, run: FrameworkRun, prefix: str, answers: dict[str, Answer], title: str):
    ver = _ss().get("p3_ver", 0)
    with st.form(f"p3_form_{prefix}_{ver}"):
        st.markdown(f"**{title}**")
        cols = st.columns(3)
        widgets = {}
        for i, (k, a) in enumerate(answers.items()):
            with cols[i % 3]:
                widgets[k] = _input(f"{prefix}.{k}", a.value, key=f"p3_{prefix}_{k}_{ver}",
                                    label=f"{k} · {c.clean(a.question)[:70]}")
        if st.form_submit_button("Save changes"):
            _apply(c, [(f"{prefix}.{k}", v) for k, v in widgets.items() if _changed(v, answers[k].value)],
                   f"p3_msg_{prefix}")


def _gate_screen(c: _Ctx):
    ss = _ss()
    run = _need_run(ss)
    if run is None:
        return
    for p in ("doc_gate", "context", "readiness_defaults"):
        _show_msg(f"p3_msg_{p}")
    m1, m2, m3 = st.columns(3)
    m1.metric("Gate score", f"{run.gate_score:g} of {len(run.doc_gate)}")
    m2.metric("Documentation confidence", run.gate_band)
    m3.metric("Map reviewed", "yes" if run.map_reviewed else "no")
    if run.gate_band == "Low":
        st.warning("Documentation confidence is Low: steps are not scored. Document the process first.")

    ends = [a for a in c.model.activities if a.kind == "end"]
    if ends:
        ids = [a.id for a in ends]
        labels = [f"{i} · {c.clean(c.label(i))}" for i in ids]
        idx = ids.index(run.success_end) if run.success_end in ids else None
        picked = st.selectbox("Success end (the end that means the process worked)", labels, index=idx,
                              placeholder="Choose the end that means the process worked…")
        pick = ids[labels.index(picked)] if picked is not None else None
        if pick is not None and pick != run.success_end:   # only an explicit choice changes it
            new = run.model_copy(deep=True)
            new.success_end = pick
            _set_run(apply_rules(new, c.model, c.s, c.names))
            st.rerun()

    st.markdown("##### Documentation gate (8 checks)")
    st.dataframe(_answer_rows(c, [(f"{k} · {GATE_LABEL.get(k, '')}", a) for k, a in run.doc_gate.items()]),
                 hide_index=True, width="stretch")
    st.markdown("##### Context")
    st.dataframe(_answer_rows(c, run.context.items()), hide_index=True, width="stretch")
    st.markdown("##### Readiness (process defaults)")
    st.dataframe(_answer_rows(c, run.readiness_defaults.items()), hide_index=True, width="stretch")
    st.markdown("##### Map checks")
    if run.map_checks:
        for mc in run.map_checks:
            st.markdown(f"- **{mc.type.replace('_', ' ')}** ({', '.join(mc.step_ids)}): {c.clean(mc.question)}")
    else:
        st.caption("No map problems found.")
    with st.expander("Change an answer"):
        st.caption("A changed answer is recorded as **answered** (high confidence) and the rules run again. No AI call.")
        _override_box(c, run, "doc_gate", run.doc_gate, "Documentation gate")
        _override_box(c, run, "context", run.context, "Context")
        _override_box(c, run, "readiness_defaults", run.readiness_defaults, "Readiness (process defaults)")


# ------------------------------------------------------------------------ 3. Steps
def _spec_value(c: _Ctx, v) -> str:
    if isinstance(v, (dict, list)):
        return c.clean(json.dumps(v, ensure_ascii=False))
    return c.clean(v)


def _spec_md(c: _Ctx, spec: dict) -> str:
    """A spec as labelled fields. A field may be a plain value or an answer-shaped dict
    ({value, basis, reason, confidence, quotes}); the latter shows its basis, reason and quote."""
    lines = [f"**{c.clean(spec.get('name', 'New step'))}** · {spec.get('type', '')}"]
    for k, v in spec.items():
        if k in ("name", "type"):
            continue
        label = k.replace("_", " ").capitalize()
        meta = ""
        if isinstance(v, dict) and "value" in v:
            basis = BASIS_BADGE.get(v.get("basis"), v.get("basis") or "")
            bits = [b for b in (basis, c.clean(v.get("reason") or "")) if b]
            quotes = [q for q in v.get("quotes") or [] if isinstance(q, dict) and q.get("quote")]
            if quotes:
                bits.append(c.clean(f'"{quotes[0]["quote"]}" ({quotes[0].get("file", "")} @ {quotes[0].get("location", "")})'))
            meta = f"  \n    ↳ {' · '.join(bits)}" if bits else ""
            v = v["value"]
        if isinstance(v, list):
            lines.append(f"- **{label}:**{meta}")
            lines += [f"    - {_spec_value(c, x)}" for x in v]
        else:
            lines.append(f"- **{label}:** {_spec_value(c, v)}{meta}")
    return "\n".join(lines)


def _steps_screen(c: _Ctx):
    ss = _ss()
    run = _need_run(ss)
    if run is None:
        return
    _low_gate_note(run)
    rows = step_table(run, c.model, c.names)
    if not rows:
        st.info("No steps to show.")
        return
    st.caption("Select a row (or pick a step below) to see every answer behind its call.")
    event = st.dataframe(pd.DataFrame(rows).astype({"wave": str, "readiness": str}), hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key="p3_steps_table")
    ids = [r["step_id"] for r in rows]
    picked = getattr(getattr(event, "selection", None), "rows", None) or []
    if picked and ids[picked[0]] != ss.get("p3_step_last_row"):
        ss.p3_step = ss.p3_step_last_row = ids[picked[0]]
    if ss.get("p3_step") not in ids:
        ss.p3_step = ids[0]
    sid = st.selectbox("Step", ids, key="p3_step", format_func=lambda i: f"{i} · {c.clean(c.label(i))}")
    stp = run.steps[sid]
    msg_key = f"p3_msg_step_{sid}"
    _show_msg(msg_key)

    row = next(r for r in rows if r["step_id"] == sid)
    st.markdown(f"#### {sid} · {c.clean(c.label(sid))}")
    facts = [f"**Call:** {stp.call or 'not scored'}" + (f" ({stp.call_rule})" if stp.call_rule else "")]
    if stp.combine_target:
        facts.append(f"**Combine with:** {stp.combine_target}")
    if stp.tier:
        facts.append(f"**Tier:** {('Standardize first → ' if stp.tier.standardize_first else '') + _tier_label(stp.tier)}")
    if stp.readiness_min is not None:
        facts.append(f"**Readiness (weakest test):** {stp.readiness_min}")
    if stp.recommendation:
        facts.append(f"**Action:** {c.clean(stp.recommendation.action)}"
                     + (f" · wave {stp.recommendation.wave}" if stp.recommendation.wave is not None else ""))
    if stp.made_unnecessary_by:
        facts.append(f"**Made unnecessary by:** {stp.made_unnecessary_by}")
    facts.append(f"**Why:** {row['reason']} (confidence {CONF.get(row['confidence'], row['confidence'])})")
    st.markdown("  \n".join(facts))

    groups: dict[str, list[tuple[str, Answer]]] = {}
    for suffix, a in _step_answers(stp):
        groups.setdefault(suffix.split(".")[0], []).append((suffix, a))
    ver = ss.get("p3_ver", 0)
    st.markdown("##### Answers")
    st.caption("Change any value and **Save** to record it as **answered**; the rules run again (no AI call).")
    for fam, items in groups.items():
        assumed_n = sum(a.basis == "assumed" for _, a in items)
        with st.expander(f"{FAMILY_LABEL.get(fam, fam)} — {len(items)} answers, {assumed_n} assumed"):
            st.dataframe(_answer_rows(c, [(s.split(".")[-1], a) for s, a in items]),
                         hide_index=True, width="stretch")
            with st.form(f"p3_form_{sid}_{fam}_{ver}"):
                cols = st.columns(3)
                widgets = {}
                for i, (suffix, a) in enumerate(items):
                    path = f"steps.{sid}.{suffix}"
                    with cols[i % 3]:
                        widgets[path] = (_input(path, a.value, key=f"p3_w_{path}_{ver}",
                                                label=suffix.split(".")[-1]), a.value)
                if st.form_submit_button("Save"):
                    _apply(c, [(p, v) for p, (v, cur) in widgets.items() if _changed(v, cur)], msg_key)
    answered = [(s, a) for s, a in _step_answers(stp) if a.basis == "answered" and a.history]
    if answered:
        with st.expander(f"Undo an answer ({len(answered)} answered here)"):
            for suffix, a in answered:
                u1, u2 = st.columns([3, 1])
                u1.markdown(f"`{suffix}` = **{c.clean(a.value)}**")
                if u2.button("Undo", key=f"p3_undo_{sid}_{suffix}_{ver}"):
                    try:
                        _set_run(undo_answer(run, c.model, f"steps.{sid}.{suffix}", c.s, names=c.names))
                        st.rerun()
                    except (ValueError, KeyError) as e:
                        st.error(str(e))

    st.markdown("##### Future state for this step")
    chain = stp.recommendation.chain if stp.recommendation and stp.call == "Keep" else []
    if chain:
        st.markdown(" → ".join(f"**{c.clean(x.name)}** ({x.type}"
                               + (f", {c.clean(c.model.role_name(x.role_id))}" if x.role_id else "") + ")"
                               for x in chain))
        types = {x.type for x in chain}
        specs = [sp for sp in stp.specs if isinstance(sp, dict) and sp.get("type") in types]
        for sp in specs:
            with st.container(border=True):
                st.markdown(_spec_md(c, sp))
        if not specs:
            st.caption("No spec written for these steps yet.")
    else:
        st.caption(f"No future chain: the call is {stp.call or 'not scored yet'}.")
    pains = [p for p in c.model.pain_points if p.node_id == sid]
    if pains:
        st.markdown("##### Stage 1 pain points on this step")
        for p in pains:
            st.markdown(f"- **{p.id}** {c.clean(p.description)} ({p.category})")


# -------------------------------------------------------------------- 4. Follow-ups
def _followups_screen(c: _Ctx):
    ss = _ss()
    run = _need_run(ss)
    if run is None:
        return
    _show_msg("p3_msg_followups")
    top = ensure_top_questions(run, c.model, c.s, c.names)     # cached on the run; recomputed only on change
    st.download_button("⬇ Download follow-ups (Markdown)", follow_ups_markdown(run, c.model, c.names, c.s),
                       file_name="follow_ups.md", mime="text/markdown", key="p3_dl_fu_top")
    if not run.follow_ups:
        st.success("No follow-up questions: every input that could change a result has evidence or an answer.")
        return
    st.markdown(f"**{top_questions_heading(len(top), len(run.follow_ups))}**")
    if not top:
        st.success("None of the open questions can change a call, tier, wave, readiness or the posture.")
        return
    st.caption("These are the questions whose answer could change a result, the biggest effect first. Answer any "
               "of them and **Save answers**; blank ones are left as they are. Saving rescores with code only — "
               "no AI call — and the list is worked out again.")
    ver = ss.get("p3_ver", 0)
    with st.form(f"p3_followups_{ver}"):
        widgets = {}
        for i, q in enumerate(top, 1):
            st.markdown(f"**{i}. {c.clean(q.question)}**")
            if q.effect:
                st.caption(c.clean(q.effect))
            cols = st.columns(min(len(q.paths), 3))
            for j, p in enumerate(q.paths):
                cur = q.current.get(p)
                shown = "none" if cur is None else c.clean(cur)
                label = c.clean(q.labels.get(p) or q.question)
                with cols[j % len(cols)]:
                    widgets[p] = _input(p, None, key=f"p3_fu_{p}_{ver}", blank=True,
                                        label=f"{label} (now: {shown}, assumed)")
        if st.form_submit_button("Save answers", type="primary"):
            _apply(c, [(p, v) for p, v in widgets.items() if _changed(v, None)], "p3_msg_followups")


# ------------------------------------------------------------------ 5. Future state
def _future_screen(c: _Ctx):
    ss = _ss()
    run = _need_run(ss)
    if run is None:
        return
    t1, t2 = st.columns([2, 3])
    show_removed = t1.toggle("Show what changed", key="p3_show_removed",
                             help="Keep removed and combined steps on the diagram, drawn dashed in grey "
                                  "(a combined step says which step it merges into)")
    zoom = t2.slider("Zoom", 0.4, 1.6, 0.8, 0.05, key="p3_zoom")
    rows = step_table(run, c.model, c.names)
    summary = summary_markdown(run, c.model, c.names, c.s)
    fm, markers = build_future_model(c.model, run, show_removed=show_removed, names=c.names)
    title = f"{c.model.process.name} — future state"
    try:   # a diagram failure keeps the table, summary and the other exports
        geo = build_geometry(fm, title=title)
        svg = render_svg(fm, markers=markers, title=title, geometry=geo)
        drawio_xml = render_drawio(fm, geo, markers)
    except Exception as ex:  # noqa: BLE001
        st.error(f"The future-state diagram couldn't be drawn ({type(ex).__name__}: {ex}). "
                 "The step table, summary and other exports below are still complete.")
    else:
        st.iframe(
            f"""<div style="overflow:auto;border:1px solid #E5E7EB;border-radius:8px;background:#fff;padding:8px">
                  <div style="transform:scale({zoom});transform-origin:0 0;width:max-content">{svg}</div></div>""",
            height=640)
        d = st.columns(4)
        d[0].download_button("⬇ draw.io (editable)", drawio_xml, file_name="future_state.drawio",
                             mime="application/xml", width="stretch", key="p3_dl_drawio")
        d[1].download_button("⬇ SVG", svg, file_name="future_state.svg", mime="image/svg+xml",
                             width="stretch", key="p3_dl_svg")
        try:
            d[2].download_button("⬇ PNG", render_png(svg), file_name="future_state.png", mime="image/png",
                                 width="stretch", key="p3_dl_png")
        except Exception:  # noqa: BLE001 - cairosvg missing
            d[2].caption("PNG export needs `cairosvg`")
        d[3].link_button("✏️ Open in draw.io", drawio_url(drawio_xml), width="stretch")
    e = st.columns(4)
    e[0].download_button("⬇ Step table (CSV)", step_table_csv(rows), file_name="step_table.csv", mime="text/csv",
                         width="stretch", key="p3_dl_csv")
    e[1].download_button("⬇ Step table (JSON)", json.dumps(rows, indent=2, ensure_ascii=False),
                         file_name="step_table.json", mime="application/json", width="stretch",
                         key="p3_dl_json")
    e[2].download_button("⬇ Summary (Markdown)", summary, file_name="summary.md", mime="text/markdown",
                         width="stretch", key="p3_dl_summary")
    e[3].download_button("⬇ Follow-ups (Markdown)", follow_ups_markdown(run, c.model, c.names, c.s),
                         file_name="follow_ups.md", mime="text/markdown", width="stretch", key="p3_dl_fu")
    f1, f2 = st.columns(2)
    f1.download_button("⬇ Full run (JSON)", c.clean(run.model_dump_json(indent=2)), file_name="framework_run.json",
                       mime="application/json", key="p3_dl_run", width="stretch")
    if c.save is not None:
        f2.download_button("💾 Save project", c.save(), file_name="cgs_project.json", mime="application/json",
                           key="p3_save_project", width="stretch",
                           help="Saves the materials, the map, the agent run and your answers so you can pick up later.")
    st.caption("⚙ Rules step · double border: Agent step · ✓ Human approval · plain: Human task. "
               "Removed and combined steps appear dashed in grey with **Show what changed**.")
    st.divider()
    with st.container(border=True):
        st.markdown(summary)


def _stale_warning(c: _Ctx, box):
    run, ss = _ss().get("framework"), _ss()
    h = run.artifacts.get("input_hash") if run is not None else None
    if h and h != framework_hash(c.model, ss.parsed, c.s):
        box.warning("The map or materials changed after this framework run. Run the framework again "
                    "(Run tab) to score the current map.")


def _sync_map_reviewed():
    """The run records the session's "Mark map as reviewed" toggle (code only, no AI, no rescore)."""
    ss = _ss()
    run = ss.get("framework")
    if run is not None and run.map_reviewed != bool(ss.get("map_reviewed")):
        run = run.model_copy()
        run.map_reviewed = bool(ss.get("map_reviewed"))
        ss.framework = run


def render_phase3(model: ProcessModel, names: list[str], save=None):
    """`save`: a callable returning the project file (JSON) for the Save project button."""
    c = _Ctx(model, names, save)
    _sync_map_reviewed()
    box = st.empty()     # filled after the tabs, so a run started on the Run tab is checked too
    tabs = st.tabs(["▶ Run", "🚦 Gate & context", "🧮 Steps", "❓ Follow-ups", "🔮 Future state"])
    for tab, fn in zip(tabs, (_run_screen, _gate_screen, _steps_screen, _followups_screen, _future_screen)):
        with tab:
            safe(fn, c)
    safe(_stale_warning, c, box)
